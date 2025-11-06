# SquashFS Parser & Extractor (SquashFS_Parser)

This script is a Python-based tool that parses **SquashFS version 4** filesystem images (.sqsh, .img, etc.) and restores files, directories, and symbolic links contained in the image to a local directory. It also supports applying metadata such as permissions/ownership/extended attributes.

## 🚀 Key Features

- **Full SquashFS v4 support**
- **Multiple compression formats supported**
  - Built-in: `gzip`, `xz`, `lzma`
  - Optional (requires extra libraries): `lz4`, `zstd`, `lzo`
- **Metadata preservation**
  - Permissions (`chmod`), owner/group (`chown`) — parses UID/GID tables
  - Extended attributes (**xattr**)
- **Recursive extraction**
  - Recreates the directory tree to match the original structure
- **Summary output**
  - Prints compression type, block size, inode/file/directory/symlink counts, total bytes written, average file size, fragment info, max directory depth, etc.

---

## 🔧 Requirements

- Python 3.8+
- (Optional) Extra libraries for additional compression formats

**requirements.txt**
```txt
lz4==4.4.4
python-lzo==1.15
zstandard==0.25.0
```

> `gzip/xz/lzma` are typically supported by the standard library or common distributions. To handle `lz4`, `zstd`, and `lzo`, you must install the packages above.

### Installation

```bash
pip install -r requirements.txt
```

---

## ▶️ Quick Start

```bash
python squashFS_parser.py <image_file> [-o <output_directory>] [--no-meta]
```

Examples:

```bash
# Basic extraction (creates ./extracted in the current directory)
python squashFS_parser.py firmware.img

# Specify output directory
python squashFS_parser.py firmware.img -o ./extracted_firmware

# Skip metadata application (do not apply permissions/ownership/xattr)
python squashFS_parser.py firmware.img --no-meta
```

---

## 🧰 Command Line Options

| Option | Description | Default |
|---|---|---|
| `-o, --output <DIR>` | Specify the output directory for extraction | `./extracted` |
| `--no-meta` | Skip applying metadata such as permissions/ownership/xattr | Apply metadata |

---

## 📦 Example Output

```
[+] SquashFS v4.0 block_size=65536 comp_id=4(xz)
[+] Extracted to ./final4_out

===== SquashFS Extract Summary =====
- Output dir            : ./final4_out
- Version               : 4.0
- Block size            : 65536 bytes
- Compression           : 4 (xz)
- Inodes (super)        : 2586
- Fragment entries (SB) : 147
- Entries extracted     : 2586
  · Directories         : 207
  · Files               : 2076
  · Symlinks            : 303
  · Other               : 0
- Total bytes written   : 45256220 (43.2 MiB)
- Avg non-empty file    : 21841.8 bytes (21.3 KiB)
- Unique tail fragments : 147
- Max directory depth   : 7
====================================
```

### Explanation of summary fields

- **Version / Block size / Compression**: Basic parameters read from the image superblock  
- **Inodes (super)**: Total number of inodes referenced by the superblock  
- **Entries extracted**: Total number of entries actually extracted (directories/files/symlinks/other)  
- **Total bytes written**: Total number of bytes written to disk  
- **Avg non-empty file**: Average size of non-empty files  
- **Fragment entries (SB)**: Number of fragment entries recorded in the superblock  
- **Unique tail fragments**: Number of unique tail fragments with no duplicates  
- **Max directory depth**: Maximum directory depth observed while traversing the tree

---

## 📝 Notes

- Applying file ownership/permission (`chown`, `chmod`) that requires root privileges may be partially restricted depending on OS/privilege environment.  
- For images that use optional compression formats (`lz4`, `zstd`, `lzo`), the corresponding Python packages must be installed for successful extraction.  
- Symbolic links are restored as links. If the link target points outside the image, the restored symlink may be broken.

---

## 🧪 Development / Debug Tips

- If the parser fails to apply certain metadata (UID/GID/xattr), run with `--no-meta` first to isolate parsing/extraction logic from metadata application.  
- When debugging, log the offsets and sizes of the `superblock`, `inode table`, and `fragment table` for the problematic image to quickly locate the root cause.

---

If you have questions or suggestions for improvement, please open an issue! 🙌
