import pyjkernel
import pyjmap

arc = pyjkernel.from_archive_file("D:/Modding/Super Mario Galaxy/SMG2/files/AudioRes/Seqs/JaiSeq.arc")

for file in arc.list_files(arc.root_name):
    print(file.name, file.preload, file.compression)
