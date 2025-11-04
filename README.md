# SquashFS 파서 및 추출기 (SquashFS_Parser)

SquashFS **버전 4** 이미지(.sqsh, .img 등)를 파싱하고, 이미지 내 파일·디렉터리·심볼릭 링크를 로컬 디스크로 복원하는 Python 기반 도구입니다. 권한/소유권/확장속성 등 메타데이터 적용도 지원합니다.

## 🚀 주요 기능

- **SquashFS v4 완전 지원**
- **다양한 압축 방식 지원**
  - 기본: `gzip`, `xz`, `lzma`
  - 선택(라이브러리 설치 필요): `lz4`, `zstd`, `lzo`
- **메타데이터 보존**
  - 권한(`chmod`), 소유자/그룹(`chown`) — UID/GID 테이블 파싱
  - 확장 속성(**xattr**)
- **재귀 추출**
  - 디렉터리 트리를 보존하여 동일한 구조로 추출
- **결과 요약 출력**
  - 압축 방식, 블록 크기, Inode/파일/디렉터리/심볼릭 링크 수, 총 기록 바이트, 평균 파일 크기, fragment 정보, 최대 디렉터리 깊이 등

---

## 🔧 요구 사항

- Python 3.8+
- (선택) 추가 압축 포맷용 라이브러리

**requirements.txt**
```txt
lz4==4.4.4
python-lzo==1.15
zstandard==0.25.0
```

> `gzip/xz/lzma`는 표준 라이브러리 혹은 일반 배포판에서 기본 지원하는 경우가 많습니다. `lz4`, `zstd`, `lzo`는 위의 패키지를 설치해야 동작합니다.

### 설치

```bash
pip install -r requirements.txt
```

---

## ▶️ 빠른 시작

```bash
python squashFS_parser.py <이미지_파일> [-o <출력_디렉터리>] [--no-meta]
```

예시:

```bash
# 기본 추출 (현재 디렉터리에 ./extracted 생성)
python squashFS_parser.py firmware.img

# 출력 디렉터리 지정
python squashFS_parser.py firmware.img -o ./extracted_firmware

# 메타데이터 적용 생략(권한/소유권/xattr 미적용)
python squashFS_parser.py firmware.img --no-meta
```

---

## 🧰 명령줄 옵션

| 옵션 | 설명 | 기본값 |
|---|---|---|
| `-o, --output <DIR>` | 추출 대상 출력 디렉터리 지정 | `./extracted` |
| `--no-meta` | 권한/소유권/xattr 등 메타데이터 적용 생략 | 적용함 |

---

## 📦 실행 결과 예시

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

### 요약 항목 설명

- **Version / Block size / Compression**: 이미지 슈퍼블록에서 읽은 기본 파라미터  
- **Inodes (super)**: 슈퍼블록이 가리키는 총 Inode 수  
- **Entries extracted**: 실제로 추출된 엔트리 총합 (디렉터리/파일/심볼릭 링크/기타)  
- **Total bytes written**: 디스크에 기록된 총 바이트 수  
- **Avg non-empty file**: 비어 있지 않은 파일들의 평균 크기  
- **Fragment entries (SB)**: 슈퍼블록에 기록된 fragment 엔트리 수  
- **Unique tail fragments**: tail fragment 중 중복되지 않는 조각 수  
- **Max directory depth**: 탐색한 디렉터리 트리의 최대 깊이

---

## 📝 참고 사항

- 루트 권한이 필요한 파일 소유권/권한 설정(`chown`, `chmod`)은 OS/권한 환경에 따라 일부 항목 적용이 제한될 수 있습니다.  
- 선택 압축 포맷(`lz4`, `zstd`, `lzo`)을 가진 이미지의 경우, 해당 파이썬 패키지가 설치되어 있어야 정상 추출됩니다.  
- 심볼릭 링크는 기본적으로 링크 자체를 복원합니다. 링크 대상이 이미지 밖에 있으면 깨진 링크가 될 수 있습니다.

---

## 🧪 개발/디버그 팁

- 파서가 특정 메타데이터(UID/GID/xattr)를 적용하지 못한다면, `--no-meta`로 동작을 분리해 파싱/추출 로직부터 확인하세요.  
- 문제가 발생한 이미지의 `superblock`/`inode table`/`fragment table` 오프셋과 크기를 로그로 출력해 추적하면 원인 파악이 빠릅니다.

---

문의나 개선 제안이 있다면 이슈로 남겨 주세요! 🙌
