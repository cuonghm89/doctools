# C-PDF Gear

Ứng dụng macOS viết bằng SwiftUI, điều khiển 1 engine Python (PyMuPDF) để
dịch PDF sang tiếng Việt ngay tại chỗ — giữ nguyên bố cục, hình ảnh/vector
gốc, ưu tiên DeepL với Gemini dự phòng.

## Cấu trúc dự án

```
Package.swift                          Manifest SPM, mở trực tiếp bằng Xcode
Sources/CPDFGear/
  CPDFGearApp.swift                    Entry point của app
  ContentView.swift                    UI kéo-thả, cài đặt, tiến độ
  TranslationRunner.swift              Chạy Python engine dưới dạng subprocess
PythonEngine/
  translator_engine.py                 CLI entry: PDF -> PDF, giữ nguyên bố cục
  router.py                            Định tuyến thông minh DeepL/Gemini + theo dõi quota
  test_router.py                       Self-check (không cần mạng), chạy trực tiếp
  requirements.txt
  .venv/                               Đã tạo & cài sẵn (gitignored)
scripts/
  package_app.sh                       Đóng gói .app + zip để release
.github/workflows/
  ci.yml                               swift build + self-check Python mỗi push/PR
  release.yml                          Build & đăng GitHub Release khi push tag
```

## 1. Cài đặt Python engine

Đã có sẵn 1 virtualenv tại `PythonEngine/.venv`. Muốn tạo lại từ đầu:

```bash
cd "PythonEngine"
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Kiểm tra nhanh logic router (không gọi mạng, không cần PDF):

```bash
./.venv/bin/python test_router.py
```

## 2. Chạy app

Mở `Package.swift` bằng Xcode (double-click, hoặc `open Package.swift`),
chọn scheme `CPDFGear` và **My Mac**, rồi Run (⌘R).

Hoặc chạy từ terminal:

```bash
swift run CPDFGear
```

## 3. Cách app tự tìm Python

Không cần cấu hình gì thêm: `PythonProcess.engineDir` (trong
[`PythonProcess.swift`](Sources/CPDFGear/PythonProcess.swift)) tự nhận diện
vị trí `PythonEngine` — lấy từ `Resources/PythonEngine` trong bundle khi
chạy 1 `.app` đã đóng gói (xem [Đóng gói thành .app](#đóng-gói-thành-app) bên
dưới), hoặc từ cây thư mục source khi chạy qua `swift run`/Xcode.

Về interpreter, `PythonProcess.resolvedPythonPath` ưu tiên runtime đóng gói
sẵn tại `Resources/PythonRuntime/bin/python3` (app đã đóng gói, deps đã cài
sẵn — xem [Đóng gói thành .app](#đóng-gói-thành-app)), rồi tới
`PythonEngine/.venv/bin/python3` nếu venv dev đó tồn tại, cuối cùng fallback
về bất kỳ `python3` nào có trong `PATH`.

## 4. Lấy API key

- **DeepL**: https://www.deepl.com/pro-api — key miễn phí kết thúc bằng
  `:fx` sẽ tự động dùng endpoint free; key trả phí dùng endpoint pro.
- **Gemini** (dự phòng cho bảng biểu, vượt quota, hoặc DeepL lỗi):
  https://aistudio.google.com/apikey

Dán cả 2 key vào panel Cài đặt. Key DeepL bắt buộc để bắt đầu dịch; key
Gemini không bắt buộc nhưng nên có khi gần chạm quota free 500.000 ký tự
của DeepL.

## Cách hoạt động

- **Router** (`router.py`): gọi thẳng API `/v2/usage` thật của DeepL để lấy
  số đã dùng/hạn mức mỗi lần bắt đầu dịch (không tự đếm bằng cache local
  nữa). Khối văn bản thường trong quota đi DeepL; khối dạng bảng, vượt
  quota, hoặc DeepL lỗi thì chuyển sang Gemini (`gemini-flash-latest`).
- **Giữ nguyên bố cục** (`translator_engine.py`): từng khối chữ trên trang
  được đọc qua `page.get_text("dict")` (bbox + cỡ chữ chính xác). Chữ cũ bị
  xoá bằng redaction PDF thật (`add_redact_annot` + `apply_redactions`, chỉ
  áp dụng cho chữ — hình ảnh và vector graphics được loại trừ rõ ràng), rồi
  chữ đã dịch được vẽ lại đúng vị trí đó bằng `insert_textbox`.
- **Dynamic Canvas**: cỡ chữ tự thu nhỏ dần (tới `original_size * Font
  Scale Factor`) cho đến khi văn bản đã dịch (word-wrap) vừa khít khung
  gốc, đo bằng 1 `Shape` chưa commit (không để lại chữ ma). Nếu đã ở cỡ nhỏ
  nhất mà vẫn cần thêm chỗ theo chiều dọc, khung sẽ giãn xuống dưới thay vì
  âm thầm cắt mất chữ.
- **Chữ tiếng Việt**: vẽ bằng font hệ thống `Arial.ttf`
  (`/System/Library/Fonts/Supplemental/`), vì font `helv` có sẵn của
  PyMuPDF không có dấu tiếng Việt.
- **Đoạn code không bị dịch** (`code_blocks.py`): nếu tài liệu chứa 1 đoạn
  mã nguồn (Python/C/Java/JS/...), phần code giữ nguyên 100%, chỉ chú
  thích (`# ...`, `// ...`, `-- ...`) bên trong được dịch. Nhận diện bằng
  heuristic (từ khoá + cú pháp phổ biến) — xem [Giới hạn đã biết](#giới-hạn-đã-biết-cố-ý-không-phải-bug).
- **API key** (`KeychainStore.swift`): lưu trong Keychain macOS, không phải
  UserDefaults/plain text.
- **Kiểm tra bản mới** (`UpdateChecker.swift`): mỗi lần mở app đã đóng gói,
  tự hỏi GitHub Releases xem có bản mới hơn không (chỉ báo, không tự tải/
  cài) — hiện 1 banner nhỏ với nút "Tải về" nếu có.
- **Kiểm tra key** (`ApiKeyValidator.swift`): nút "Kiểm tra key" cạnh mỗi ô
  DeepL/Gemini gọi thử 1 endpoint nhẹ (không tốn quota dịch thật) để báo
  ngay key còn dùng được hay không, thay vì phải chờ dịch xong mới biết.
- **Bảng THẬT dịch theo từng Ô** (`find_real_tables()`/`build_table_cell_units()`
  trong `translator_engine.py`): bảng có đường kẻ lưới thật (phát hiện qua
  `page.find_tables()`) được dịch/vẽ riêng theo từng ô, tách khỏi pipeline
  đoạn văn thường — `page.get_text("dict")` tự đọc SAI THỨ TỰ cho bảng
  nhiều dòng-cao-khác-nhau (nhãn hàng lẫn vào nội dung ô kế tiếp) trước cả
  khi code chạm vào, không heuristic đoạn văn nào sửa được. Hàng bị tách
  do word-wrap được gộp lại dựa trên dải màu nền xen kẽ thật (vector
  graphics), và mọi ô trong cùng 1 cột được canh về đúng 1 mốc x0 (gom
  cụm theo khoảng cách, không theo chỉ số cột — `find_tables()` có thể
  báo chỉ số cột khác nhau cho cùng 1 cột hiển thị).
- **Bảng-ẢNH dịch qua OCR** (`image_table_translate.py::
  translate_image_tables()`): bảng vẽ dưới dạng ảnh (screenshot dán vào
  tài liệu, không phải chữ thật) được OCR bằng Vision (`ocr_cli`), dựng
  lại lưới hàng/cột theo 2 đường tuỳ ảnh — ảnh CÓ viền vẽ thật (vd. bảng
  SOC-1/SOC-2) dò trực tiếp bằng pixel, chính xác nhất; ảnh KHÔNG có viền
  (vd. bảng STRIDE, chỉ màu nền xen kẽ) suy đoán từ VỊ TRÍ chữ OCR — rồi
  dịch từng ô, xoá ảnh gốc vẽ lại bằng vector thật: nền lấy mẫu từ ảnh
  gốc, VIỀN LƯỚI vẽ lại đúng màu gốc nếu ảnh có viền thật, hàng tiêu đề in
  đậm + màu chữ lấy mẫu riêng, phần thân dùng 1 màu chữ đồng nhất (mặc
  định đen, khớp quy ước phổ biến của tài liệu thay vì lấy mẫu từng ô dễ
  bị nhiễu), mọi ô canh giữa theo chiều dọc. Ảnh nào bị nhiều lớp ảnh
  chồng lồng nhau (kiểu ghép khung viền + nội dung khi export từ
  PowerPoint/Word) chỉ xử lý 1 lần trên lớp lớn nhất, tránh dịch chồng 2
  lần. Ảnh không đủ tin cậy là bảng (ít chữ, không thấy lưới rõ) giữ
  nguyên không đụng tới.

## Giới hạn đã biết (cố ý, không phải bug)

- Heuristic phân biệt bảng/đoạn văn (`is_table_block`) chỉ là kiểm tra đơn
  giản theo số dòng/mật độ chữ số — đủ để định tuyến các bảng rõ ràng sang
  Gemini, nhưng sẽ phân loại sai vài trường hợp. Thay bằng công cụ nhận
  diện bảng thật (vd. `pdfplumber`) nếu việc này quan trọng với tài liệu
  của bạn.
- Nhận diện code (`is_code_snippet`) cũng là heuristic, không phải parser
  thật cho từng ngôn ngữ: comment nằm trong 1 chuỗi ký tự dài/nhiều dòng có
  thể bị tách nhầm, và comment khối nhiều dòng (`/* ... */` tràn dòng,
  `<!-- -->`) không được xử lý — dòng đó giữ nguyên, không dịch.
- Bảng vẽ dưới dạng ẢNH (screenshot dán vào tài liệu) không thấy chữ thật
  qua `get_text()`, nhưng nếu OCR ra đủ chữ VÀ dựng được lưới ≥2 cột x ≥2
  hàng thì vẫn được tự động dịch (`image_table_translate.py`, xem README
  mục bên dưới) — chỉ ảnh quá ít chữ hoặc không thấy lưới rõ (nghi logo/
  sơ đồ/ảnh chụp thường) mới giữ nguyên không đụng tới.
- API key được lưu trong **Keychain** (`KeychainStore.swift`, không còn
  `UserDefaults`/plain text). Vì app chỉ ký ad-hoc (chưa có Apple Developer
  ID), chữ ký đổi mỗi lần đóng gói lại → macOS coi mỗi bản release là "app
  khác", có thể bật hộp thoại xin **mật khẩu đăng nhập Mac** để cấp quyền
  Keychain lần đầu bản đó chạm tới key đã lưu — không phải mỗi lần mở app,
  chỉ 1 lần/bản release (bấm "Always Allow"). Có Developer ID thật (chữ ký
  ổn định qua mọi bản) sẽ hết hẳn trường hợp này.
- Khung redaction giãn xuống dưới khi tràn chữ đôi khi có thể đè lên dòng
  bên dưới ở trang quá dày đặc — chấp nhận được với mục tiêu giữ 95% độ
  trung thực bố cục, không phải pixel-perfect mọi trang.

## Đóng gói thành .app

```bash
./scripts/package_app.sh [version]
```

Build bản release, tạo `AppIcon.icns` từ
`Sources/CPDFGear/Resources/AppIcon.png`, đóng gói các file `.py` của
`PythonEngine` (không có `.venv`, không có `test_*.py`) cùng **1 bản Python
3.12 runtime tự chứa** (tải từ
[astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone),
đã cài sẵn deps từ `requirements.txt`, đã strip debug symbols + ký lại
từng `.so`/`.dylib` để giảm dung lượng) vào `Resources/`, ghi `Info.plist`,
ký ad-hoc, rồi nén thành `dist/CPDFGear-<version>-macos.zip`. Khoảng 110MB
sau khi nén (phần lớn do `opencv-python-headless` mà `pdf2docx` phụ thuộc —
không gỡ được vì `cv2` hard-link trực tiếp tới các thư viện codec đó, gỡ ra
là `import cv2` lỗi thiếu dylib ngay), chỉ hỗ trợ macOS Apple Silicon (chưa
có universal binary — cần Xcode.app thật, máy build hiện chỉ có Command
Line Tools).

Người nhận app **không cần cài gì cả** — Python + toàn bộ thư viện
(pymupdf/pdf2docx/python-docx/python-pptx) đã nằm sẵn trong `.app`, không
đụng gì tới Python hệ thống của họ.

Vì chưa có Apple Developer ID nên app chỉ được ký **ad-hoc** (không
notarize) — lần đầu mở trên máy khác, macOS Gatekeeper sẽ chặn; mở bằng
chuột phải > **Open** (hoặc `xattr -cr CPDFGear.app`) một lần duy nhất.

## CI/CD

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — mọi push/PR:
  `swift build` + chạy self-check `PythonEngine/test_*.py`.
- [`.github/workflows/release.yml`](.github/workflows/release.yml) — push
  tag `v*.*.*` → chạy `scripts/package_app.sh`, đính file zip vào 1 GitHub
  Release tự động.

**Luồng Dev → Prod:**

```
feature/xyz  ──PR──▶  dev  ──PR (đã test ổn)──▶  main  ──tag vX.Y.Z──▶  Release
```

- Code mới: nhánh `feature/...` từ `dev`, PR vào `dev` (CI chạy tự động).
- Khi `dev` ổn định, PR `dev` → `main`.
- Release cho người dùng: `git tag vX.Y.Z && git push origin vX.Y.Z` trên
  `main` → GitHub Actions tự build + tạo Release kèm file zip cài được ngay.
