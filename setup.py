import setuptools

with open("README.md", "r") as f:
    README = f.read()

setuptools.setup(
    name="pyjkernel",
    version="0.2.1",
    author="Aurum",
    url="https://github.com/SunakazeKun/pyjkernel",
    description="Python library for Nintendo's JKRArchive/ResourceArchive format",
    long_description=README,
    long_description_content_type="text/markdown",
    keywords=["nintendo", "jkernel", "rarc", "archive", "modding"],
    packages=setuptools.find_packages(),
    install_requires=[
        "oead"
    ],
    python_requires=">=3.6",
    license="gpl-3.0",
    classifiers=[
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Programming Language :: Python :: 3 :: Only"
    ],
    entry_points={
        "console_scripts": [
            "pyjkernel = pyjkernel.__main__:main"
        ]
    }
)
