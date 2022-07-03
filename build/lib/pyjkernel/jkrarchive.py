import enum
import struct

from . import jkrcomp

__all__ = [
    "JKRArchiveException", "JKRArchive", "JKRArchiveFile", "JKRPreloadType", "create_new_archive",
    "from_buffer", "from_archive_file", "write_archive_file", "write_archive_buffer"
]


# ----------------------------------------------------------------------------------------------------------------------
# Exception for JKRArchive-related actions
# ----------------------------------------------------------------------------------------------------------------------
class JKRArchiveException(Exception):
    """
    Signals that an error occurred during any JKRArchive-related action.
    """
    pass


# ----------------------------------------------------------------------------------------------------------------------
# Helper methods to deal with reading and writing
# ----------------------------------------------------------------------------------------------------------------------
def _file_name_to_hash_(file_name: str) -> int:
    file_hash = 0
    for ch in file_name.encode("ascii"):
        file_hash *= 3
        file_hash += ch
    return file_hash & 0xFFFF


def _calc_dir_identifier_(dir_name: str, is_root: bool) -> int:
    # Root node uses "ROOT" as identifier
    if is_root:
        return 0x524F4F54

    enc_upper = dir_name.upper().encode("ascii")
    len_enc_name = len(enc_upper)

    identifier = 0
    for i in range(4):
        identifier <<= 8
        if i >= len_enc_name:
            identifier += 0x20
        else:
            identifier += enc_upper[i]

    return identifier


def _read_string_(data, offset: int) -> str:
    end = offset
    while end < len(data) - 1 and data[end] != 0:
        end += 1
    return data[offset:end + 1].decode("ascii").strip("\0")


def _get_aligned_size_(val: int) -> int:
    a = val & 31
    if a:
        return val + (32 - a)
    return val


def _get_alignment_(data) -> bytes:
    pad_len = len(data) & 31
    return bytes((32 - pad_len) if pad_len else 0)


# ----------------------------------------------------------------------------------------------------------------------
# Folder node and directory structures
# ----------------------------------------------------------------------------------------------------------------------
class JKRFolderNode:
    __STRUCT_BE__ = struct.Struct(">2I2HI")
    __STRUCT_LE__ = struct.Struct("<2I2HI")

    def __init__(self):
        self._identifier_ = 0
        self._off_name_ = 0         # Updated when packing the archive
        self._hash_ = 0             # Automatically updated when packing the entry
        self._num_files_ = 0        # Automatically updated when packing the entry
        self._idx_files_start_ = 0  # Updated whenever a file gets created, deleted, etc.

        self._archive_ = None       # The archive to which this node belongs
        self._name_ = ""            # The node's name
        self._dirs_ = list()        # The directories held by this node

    def _unpack_(self, data, off: int, is_big_endian: bool):
        strct = self.__STRUCT_BE__ if is_big_endian else self.__STRUCT_LE__
        self._identifier_, self._off_name_, self._hash_, self._num_files_, self._idx_files_start_ = strct.unpack_from(data, off)

    def _pack_(self, data, off: int, is_big_endian: bool):
        strct = self.__STRUCT_BE__ if is_big_endian else self.__STRUCT_LE__

        self._identifier_ = _calc_dir_identifier_(self._name_, self._archive_._root_ == self)
        self._hash_ = _file_name_to_hash_(self._name_)
        self._num_files_ = len(self._dirs_)
        strct.pack_into(data, off, self._identifier_, self._off_name_, self._hash_, self._num_files_, self._idx_files_start_)


class JKRDirEntry:
    __STRUCT_BE__ = struct.Struct(">2H4I")
    __STRUCT_LE__ = struct.Struct("<2H4I")

    def __init__(self):
        self._index_ = 0xFFFF     # Updated whenever a file gets created, deleted, etc.
        self._hash_ = 0           # Automatically updated when packing the entry
        self._attributes_ = 0     # Automatically updated when packing the entry
        self._off_name_ = 0       # Updated when packing the archive
        self._off_file_data_ = 0  # For file directories, this is updated when packing the archive
        self._len_file_data_ = 0  # Automatically updated when packing the entry
        self._unk10_ = 0          # Not used as of now

        self._archive_ = None     # The archive to which this directory belongs
        self._name_ = ""          # The directory's name
        self._parent_ = None      # The parent node to which this directory belongs
        self._node_ = None        # The node that this directory represents (only for folders)
        self._file_ = None        # The file info that this directory represents (only for files)

    def _unpack_(self, data, off: int, is_big_endian: bool):
        strct = self.__STRUCT_BE__ if is_big_endian else self.__STRUCT_LE__
        self._index_, self._hash_, self._attributes_, self._off_file_data_, self._len_file_data_, self._unk10_ = strct.unpack_from(data, off)
        self._off_name_ = self._attributes_ & 0x00FFFFFF
        self._attributes_ = self._attributes_ >> 24

    def _pack_(self, data, off: int, is_big_endian: bool):
        strct = self.__STRUCT_BE__ if is_big_endian else self.__STRUCT_LE__

        if self._file_ is not None:
            self._attributes_ = JKRDirAttr.FILE
            self._len_file_data_ = len(self._file_.data)

            if self._file_.compression == jkrcomp.JKRCompression.SZS:
                self._attributes_ |= JKRDirAttr.COMPRESSED | JKRDirAttr.USE_SZS
            elif self._file_.compression == jkrcomp.JKRCompression.SZP:
                self._attributes_ |= JKRDirAttr.COMPRESSED

            if self._file_.preload == JKRPreloadType.MRAM:
                self._attributes_ |= JKRDirAttr.PRELOAD_MRAM
            elif self._file_.preload == JKRPreloadType.ARAM:
                self._attributes_ |= JKRDirAttr.PRELOAD_ARAM
            elif self._file_.preload == JKRPreloadType.DVD:
                self._attributes_ |= JKRDirAttr.PRELOAD_DVD
        else:
            self._attributes_ = JKRDirAttr.FOLDER

        self._hash_ = _file_name_to_hash_(self._name_)
        attr = (self._attributes_ << 24) | self._off_name_
        strct.pack_into(data, off, self._index_, self._hash_, attr, self._off_file_data_, self._len_file_data_, self._unk10_)


class JKRDirAttr(enum.IntFlag):
    FILE         = 1
    FOLDER       = 2
    COMPRESSED   = 4
    _8           = 8
    PRELOAD_MRAM = 16
    PRELOAD_ARAM = 32
    PRELOAD_DVD  = 64
    USE_SZS      = 128


# ----------------------------------------------------------------------------------------------------------------------
# Simplified file accessor
# ----------------------------------------------------------------------------------------------------------------------
class JKRPreloadType(enum.IntEnum):
    MRAM = 0
    ARAM = 1
    DVD = 2


class JKRArchiveFile:
    def __init__(self):
        self.data = None
        self.preload = JKRPreloadType.MRAM
        self._dir_ = None

    def __repr__(self):
        return self._dir_._name_

    @property
    def name(self):
        return self._dir_._name_

    @property
    def archive(self):
        return self._dir_._archive_

    @property
    def index(self):
        return self._dir_._index_

    @property
    def compression(self):
        return jkrcomp.check_compression(self.data)


# ----------------------------------------------------------------------------------------------------------------------
# JKRArchive implementation according to the RARC / ResourceArchive format
# ----------------------------------------------------------------------------------------------------------------------
class JKRArchive:
    __STRUCT_HEADER_BE__ = struct.Struct(">8I")
    __STRUCT_HEADER_LE__ = struct.Struct("<8I")
    __STRUCT_INFO_BE__ = struct.Struct(">6IH?")
    __STRUCT_INFO_LE__ = struct.Struct("<6IH?")
    __MAGIC__ = 0x52415243

    def __init__(self):
        self.sync_file_ids = True

        self._root_ = None
        self._nodes_ = list()
        self._dirs_ = list()

        self._next_file_id_ = 0
        self._mram_files_ = list()
        self._aram_files_ = list()
        self._dvd_files_ = list()

    def _unpack_(self, data, is_big_endian: bool):
        data = jkrcomp.decompress(data)

        if struct.unpack_from(">I" if is_big_endian else "<I", data, 0)[0] != self.__MAGIC__:
            raise JKRArchiveException("No RARC data provided.")

        # Prepare correct structures
        strct_header = self.__STRUCT_HEADER_BE__ if is_big_endian else self.__STRUCT_HEADER_LE__
        strct_info = self.__STRUCT_INFO_BE__ if is_big_endian else self.__STRUCT_INFO_LE__

        # Parse RARC header and info block
        _, _, off_info, len_info, _, _, _, _\
            = strct_header.unpack_from(data, 0)
        num_nodes, off_nodes, num_dirs, off_files, _, off_strings, self._next_file_id_, self.sync_file_ids\
            = strct_info.unpack_from(data, off_info)

        # Calculate absolute offsets
        off_nodes += off_info
        off_files += off_info
        off_strings += off_info
        off_data = off_info + len_info

        # Parse folder nodes
        off_tmp = off_nodes

        for _ in range(num_nodes):
            folder_node = JKRFolderNode()
            folder_node._unpack_(data, off_tmp, is_big_endian)
            folder_node._name_ = _read_string_(data, off_strings + folder_node._off_name_)
            folder_node._archive_ = self
            off_tmp += 0x10

            if self._root_ is None:
                self._root_ = folder_node

            self._nodes_.append(folder_node)

        # Parse dir entries
        off_tmp = off_files

        for _ in range(num_dirs):
            dir_entry = JKRDirEntry()
            dir_entry._unpack_(data, off_tmp, is_big_endian)
            dir_entry._name_ = _read_string_(data, off_strings + dir_entry._off_name_)
            dir_entry._archive_ = self
            off_tmp += 0x14

            self._dirs_.append(dir_entry)
            attrs = dir_entry._attributes_

            # Folder entry
            if attrs & JKRDirAttr.FOLDER and dir_entry._off_file_data_ != 0xFFFFFFFF:
                dir_entry._node_ = self._nodes_[dir_entry._off_file_data_]

            # File entry
            elif attrs & JKRDirAttr.FILE:
                file_access = JKRArchiveFile()
                dir_entry._file_ = file_access
                file_access._dir_ = dir_entry

                # Read file data
                file_start = off_data + dir_entry._off_file_data_
                file_end = file_start + dir_entry._len_file_data_
                file_access.data = jkrcomp.decompress(data[file_start:file_end])

                # Get preload type
                if attrs & JKRDirAttr.PRELOAD_MRAM:
                    file_access.preload = JKRPreloadType.MRAM
                elif attrs & JKRDirAttr.PRELOAD_ARAM:
                    file_access.preload = JKRPreloadType.ARAM
                elif attrs & JKRDirAttr.PRELOAD_DVD:
                    file_access.preload = JKRPreloadType.DVD
                else:
                    raise JKRArchiveException(f"File {dir_entry._name_} has no preload type!")

        # Attach dir entries to nodes
        for folder_node in self._nodes_:
            for i in range(folder_node._idx_files_start_, folder_node._idx_files_start_ + folder_node._num_files_):
                dir_entry = self._dirs_[i]
                dir_entry._parent_ = folder_node
                folder_node._dirs_.append(dir_entry)

    def _pack_(self, is_big_endian: bool) -> bytearray:
        # Sort files into respective preload memory groups
        for dir_entry in self._dirs_:
            if dir_entry._file_ is not None:
                if dir_entry._file_.preload == JKRPreloadType.MRAM:
                    self._mram_files_.append(dir_entry)
                elif dir_entry._file_.preload == JKRPreloadType.ARAM:
                    self._aram_files_.append(dir_entry)
                elif dir_entry._file_.preload == JKRPreloadType.DVD:
                    self._dvd_files_.append(dir_entry)

        # Gather information about the archive and prepare output buffer
        num_nodes = len(self._nodes_)
        off_nodes = 0x20
        num_dirs = len(self._dirs_)
        off_dirs = _get_aligned_size_(off_nodes + num_nodes * 0x10)
        total_file_size = _get_aligned_size_(0x20 + off_dirs + num_dirs * 0x14)
        off_strings = total_file_size - 0x20

        buffer = bytearray(total_file_size)

        # Collect strings; pool always starts out like this
        string_pool = bytearray()
        string_pool += f".\0..\0{self._root_._name_}\0".encode("ascii")
        self._root_._off_name_ = 5

        def collect_strings(folder_node: JKRFolderNode):
            nonlocal string_pool

            for dir_entry in folder_node._dirs_:
                if dir_entry._name_ == ".":
                    dir_entry._off_name_ = 0
                elif dir_entry._name_ == "..":
                    dir_entry._off_name_ = 2
                else:
                    dir_entry._off_name_ = len(string_pool)
                    string_pool += (dir_entry._name_ + "\0").encode("ascii")

                if dir_entry._file_ is None and dir_entry._name_ not in [".", ".."]:
                    dir_entry._node_._off_name_ = dir_entry._off_name_
                    collect_strings(dir_entry._node_)

        collect_strings(self._root_)
        string_pool += _get_alignment_(string_pool)
        buffer += string_pool
        len_strings = len(string_pool)
        del string_pool

        # Write folder nodes
        off_tmp = 0x40

        for folder_node in self._nodes_:
            folder_node._pack_(buffer, off_tmp, is_big_endian)
            off_tmp += 0x10

        # Write file data
        data_start = len(buffer)

        def write_file_data(files: list) -> int:
            nonlocal buffer
            categorized_data_start = len(buffer)

            for dir_entry in files:
                dir_entry._off_file_data_ = len(buffer) - data_start
                buffer += dir_entry._file_.data
                buffer += _get_alignment_(dir_entry._file_.data)

            files.clear()
            return len(buffer) - categorized_data_start

        mram_size = write_file_data(self._mram_files_)
        aram_size = write_file_data(self._aram_files_)
        dvd_size = write_file_data(self._dvd_files_)

        # Write dir entries
        off_tmp = 0x20 + off_dirs

        for dir_entry in self._dirs_:
            dir_entry._pack_(buffer, off_tmp, is_big_endian)
            off_tmp += 0x14

        # Write header and info block
        strct_header = self.__STRUCT_HEADER_BE__ if is_big_endian else self.__STRUCT_HEADER_LE__
        strct_info = self.__STRUCT_INFO_BE__ if is_big_endian else self.__STRUCT_INFO_LE__
        total_file_size = len(buffer)

        strct_header.pack_into(buffer, 0, self.__MAGIC__, total_file_size, 0x20, data_start - 0x20, total_file_size - data_start, mram_size, aram_size, dvd_size)
        strct_info.pack_into(buffer, 0x20, num_nodes, off_nodes, num_dirs, off_dirs, len_strings, off_strings, self._next_file_id_, self.sync_file_ids)

        return buffer

    # ------------------------------------------------------------------------------------------------------------------
    # Helper functions for structure
    # ------------------------------------------------------------------------------------------------------------------
    def _fix_nodes_and_directories_(self):
        self._dirs_.clear()
        self._fix_node_and_directories_(self._root_)

        if self.sync_file_ids:
            self._next_file_id_ = len(self._dirs_)

            for i, dir_entry in enumerate(self._dirs_):
                if dir_entry._file_ is not None:
                    dir_entry._index_ = i
                else:
                    node_index = self._nodes_.index(dir_entry._node_) if dir_entry._node_ is not None else 0xFFFFFFFF
                    dir_entry._off_file_data_ = node_index
        else:
            file_id = 0

            for dir_entry in self._dirs_:
                if dir_entry._file_ is not None:
                    dir_entry._index_ = file_id
                    file_id += 1
                else:
                    node_index = self._nodes_.index(dir_entry._node_) if dir_entry._node_ is not None else 0xFFFFFFFF
                    dir_entry._off_file_data_ = node_index

            self._next_file_id_ = file_id

    def _fix_node_and_directories_(self, folder_node: JKRFolderNode):
        # Put the shortcut directories ("." and "..") at the end of the node's directory list
        shortcuts = filter(lambda de: de._file_ is None and de._name_ in [".", ".."], folder_node._dirs_)
        folders = filter(lambda de: de._file_ is None and de._name_ not in [".", ".."], folder_node._dirs_)

        for subdir in shortcuts:
            subdir._index_ = 0xFFFF
            folder_node._dirs_.remove(subdir)
            folder_node._dirs_.append(subdir)

        # Update the node's directory span
        folder_node._idx_files_start_ = len(self._dirs_)
        self._dirs_ += folder_node._dirs_

        # Handle subfolders as well
        for subdir in folders:
            subdir._index_ = 0xFFFF
            self._fix_node_and_directories_(subdir._node_)

    def _create_dir_entry_(self, name: str, attr: int, node: JKRFolderNode, parent_node: JKRFolderNode) -> JKRDirEntry:
        dir_entry = JKRDirEntry()
        dir_entry._name_ = name
        dir_entry._attributes_ = attr
        dir_entry._node_ = node
        dir_entry._parent_ = parent_node
        dir_entry._archive_ = self

        parent_node._dirs_.append(dir_entry)

        return dir_entry

    def _create_root_(self, root_name: str):
        if self._root_:
            raise JKRArchiveException("Fatal! Root already exists!")

        root_node = JKRFolderNode()
        root_node._name_ = root_name
        root_node._archive_ = self
        self._root_ = root_node
        self._nodes_.append(root_node)

        self._create_dir_entry_(".", JKRDirAttr.FOLDER, root_node, root_node)
        self._create_dir_entry_("..", JKRDirAttr.FOLDER, None, root_node)

        self._fix_nodes_and_directories_()

    def _find_folder_(self, folder_path: str) -> JKRFolderNode:
        split_path = folder_path.split("/")

        # Only root folder name provided
        root_name = split_path[0]

        if len(split_path) == 1:
            if self._root_._name_.casefold() == root_name.casefold():
                return self._root_
            return None

        folders = split_path[1:-1]
        folder_name = split_path[-1]

        if self._root_._name_.casefold() != root_name.casefold():
            return None

        # Walk through folder nodes
        folder_node = self._root_

        for sub_folder_name in folders:
            next_folder = None

            # Find subfolder
            for dir_entry in folder_node._dirs_:
                if dir_entry._name_.casefold() == sub_folder_name.casefold():
                    if dir_entry._file_ is None:
                        next_folder = dir_entry._node_
                    break

            if next_folder is None:
                return None

            folder_node = next_folder

        # Locate file in folder node
        for dir_entry in folder_node._dirs_:
            if dir_entry._name_.casefold() == folder_name.casefold():
                if dir_entry._file_ is None:
                    return dir_entry._node_
                return None

        return None

    # ------------------------------------------------------------------------------------------------------------------
    # High level operations
    # ------------------------------------------------------------------------------------------------------------------
    def get_file(self, file_path: str) -> JKRArchiveFile:
        split_path = file_path.rsplit("/", 1)

        # Only root folder name provided. Root cannot be a file
        if len(split_path) == 1:
            return None

        folder_path, file_name = split_path
        folder_node = self._find_folder_(folder_path)

        if folder_node is None:
            return None

        # Locate file in folder node
        for dir_entry in folder_node._dirs_:
            if dir_entry._name_.casefold() == file_name.casefold():
                if dir_entry._file_ is not None:
                    return dir_entry._file_
                return None

        return None

    def exists_file(self, file_path: str) -> bool:
        return self.get_file(file_path) is not None

    def create_folder(self, folder_path: str):
        split_path = folder_path.rsplit("/", 1)

        # Only root folder name provided
        if len(split_path) == 1:
            return None

        folder_path, folder_name = split_path
        folder_node = self._find_folder_(folder_path)

        if folder_node is None:
            raise JKRArchiveException("Cannot create folder. Archive does not contain the folder " + folder_path)

        # Check if directory already exists
        for dir_entry in folder_node._dirs_:
            if dir_entry._name_.casefold() == folder_name.casefold():
                raise JKRArchiveException(f"The directory {folder_path} already exists.")

        # Create new node and directories
        new_folder_node = JKRFolderNode()
        new_folder_node._name_ = folder_name
        new_folder_node._archive_ = self
        self._nodes_.append(new_folder_node)

        self._create_dir_entry_(folder_name, JKRDirAttr.FOLDER, new_folder_node, folder_node)
        self._create_dir_entry_(".", JKRDirAttr.FOLDER, new_folder_node, new_folder_node)
        self._create_dir_entry_("..", JKRDirAttr.FOLDER, folder_node, new_folder_node)

        self._fix_nodes_and_directories_()

    def create_file(self, file_path: str) -> JKRArchiveFile:
        split_path = file_path.rsplit("/", 1)

        # Only root folder name provided. Root cannot be a file
        if len(split_path) == 1:
            return None

        folder_path, file_name = split_path
        folder_node = self._find_folder_(folder_path)

        if folder_node is None:
            raise JKRArchiveException("Cannot create file. Archive does not contain the folder " + folder_path)

        # Check if directory already exists
        for dir_entry in folder_node._dirs_:
            if dir_entry._name_.casefold() == file_name.casefold():
                raise JKRArchiveException(f"The directory {file_path} already exists.")

        # Create new directory and file entries
        dir_entry = self._create_dir_entry_(file_name, JKRDirAttr.FILE, None, folder_node)
        dir_entry._file_ = JKRArchiveFile()
        dir_entry._file_.data = bytearray()
        dir_entry._file_.preload = JKRPreloadType.MRAM

        self._fix_nodes_and_directories_()
        return dir_entry._file_


# ----------------------------------------------------------------------------------------------------------------------
# Helper I/O and creation functions
# ----------------------------------------------------------------------------------------------------------------------
def from_buffer(buffer, big_endian: bool = True) -> JKRArchive:
    jkrarc = JKRArchive()
    jkrarc._unpack_(buffer, big_endian)
    return jkrarc


def from_archive_file(file_path: str, big_endian: bool = True) -> JKRArchive:
    jkrarc = JKRArchive()
    with open(file_path, "rb") as f:
        jkrarc._unpack_(f.read(), big_endian)
    return jkrarc


def write_archive_buffer(jkrarc: JKRArchive, big_endian: bool = True):
    return jkrarc._pack_(big_endian)


def write_archive_file(jkrarc: JKRArchive, file_path: str, big_endian: bool = True):
    buffer = jkrarc._pack_(big_endian)

    with open(file_path, "wb") as f:
        f.write(buffer)
        f.flush()


def create_new_archive(root_name: str, sync_file_ids: bool = True):
    jkrarc = JKRArchive()
    jkrarc.sync_file_ids = sync_file_ids
    jkrarc._create_root_(root_name)
    return jkrarc
