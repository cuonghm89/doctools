# CODEMAP — C-PDF Gear

Tra cứu nhanh "hàm nào gọi hàm nào" mà không cần đọc lại toàn bộ code. Xem
`SKELETON.md` trước để có bức tranh tổng thể (cấu trúc thư mục, sơ đồ module,
luồng chạy chính). Cập nhật file này khi thêm/sửa hàm quan trọng.

---

## Swift — Sources/CPDFGear

### CPDFGearApp.swift
- `AppDelegate.applicationDidFinishLaunching` — ép app SPM lên foreground
  (`NSApp.setActivationPolicy(.regular)`, `.activate()`). Chỉ set
  `NSApp.applicationIconImage` runtime khi CHƯA có `CFBundleIconFile` trong
  Info.plist (tức đang chạy dev qua `swift run`/Xcode — app đóng gói qua
  `scripts/package_app.sh` đã tự có icon từ `AppIcon.icns`). Đọc
  `Resources/AppIcon.png` qua đường dẫn suy từ `#filePath`, KHÔNG dùng
  `Bundle.module`: accessor đó do SwiftPM tự sinh, tìm bundle resource ngay
  tại `Bundle.main.bundleURL` — hợp lệ khi chạy dev (bundle nằm cạnh
  executable trong `.build/`) nhưng crash cứng (`fatalError`) trong 1 `.app`
  đã đóng gói, và nếu cố chép bundle đó vào thì `codesign` từ chối ký hẳn
  (không cho phép nội dung nằm ngoài `Contents/`). Đã tái hiện + xác nhận cả
  2 lỗi bằng crash log và `codesign` thật trước khi sửa. `Package.swift`
  không còn khai báo `resources:` cho target này nữa.
- `CPDFGearApp` (struct `App`) — entry point, tạo `ContentView`.

### ContentView.swift
- Sở hữu `@StateObject runner: TranslationRunner`, `@StateObject converter:
  ConversionRunner`, `@StateObject history: HistoryStore`, `@StateObject
  updateChecker: UpdateChecker`, `@State queue: [URL]` (hàng đợi file chờ
  xử lý, sửa được khi chưa chạy).
- `deeplApiKey`/`geminiApiKey` (`@State`, bắt đầu rỗng) — lưu thật ở
  Keychain (`KeychainStore.swift`), KHÔNG dùng `@AppStorage` (UserDefaults)
  nữa. Nạp bất đồng bộ qua `loadApiKeysFromKeychain()` ở `.onAppear`, không
  nạp thẳng trong initializer — macOS có thể bật hộp thoại xin mật khẩu xin
  quyền Keychain (đặc biệt sau mỗi lần đóng gói lại, chữ ký ad-hoc đổi mỗi
  build khiến macOS coi là "app khác" cần hỏi lại quyền); gọi trên main
  thread lúc init sẽ treo cứng cả cửa sổ (không hiện ra) cho tới khi người
  dùng phản hồi — đã tái hiện + xác nhận bằng cách chạy app thật, fix bằng
  cách nạp trên background thread (`Task.detached`) rồi gán lại `@State`
  qua `MainActor.run`. `isLoadingApiKeys` chặn `.onChange` tự ghi ngược lại
  Keychain giá trị vừa đọc xong.
- `keyTestRow(isChecking:result:onTest:)` — nút "Kiểm tra key" + hiện kết
  quả (`ApiKeyValidator.Outcome`, xem `ApiKeyValidator.swift`). `testKey(_:
  isChecking:result:validate:)` — chạy `validate` (async) trên background,
  dùng chung cho cả DeepL (`ApiKeyValidator.validateDeepL`) và Gemini
  (`.validateGemini`) qua tham số truyền vào. Gọi bởi: `settingsCard`.
- `canStart` — chặn khi `queue` rỗng/thiếu DeepL key/runner/converter đang
  chạy. `canConvert` — chặn khi `pdfsInQueue` rỗng (xuất Word/PPTX chỉ nhận
  input PDF) hoặc runner/converter đang chạy.
- `body` → ghép các view con: `header`, `updateBanner(latestVersion:)` (chỉ
  hiện khi `updateChecker.latestVersion != nil`), `dropZone`, `queueList`,
  `settingsCard`, `statusArea`, `startButton`, `convertButtons`,
  `conversionStatusArea`, `historyCard`. `.onAppear` gắn `runner.onItemDone`
  / `converter.onItemDone` để ghi vào `history`, gọi `updateChecker.check()`
  và `loadApiKeysFromKeychain()`.
- `updateBanner(latestVersion:)` — banner nhỏ, nút "Tải về" mở
  `updateChecker.releaseURL` bằng `NSWorkspace.shared.open()`.
- `dropZone` — nhận nhiều file thả cùng lúc, PDF/`.docx`/`.pptx` (dịch hỗ
  trợ cả 3 — xem `office_translate.py`; lọc theo `Self.supportedExtensions`
  trong `onDrop`, loop qua mọi `provider`), hoặc bấm "Chọn file..." →
  `pickFiles()` (NSOpenPanel `allowsMultipleSelection = true`,
  `allowedContentTypes` gồm `.pdf` + UTType của `docx`/`pptx`). Cả 2 gọi
  `addToQueue(_:)`.
- `addToQueue(_:)` — thêm 1 URL vào `queue`, bỏ qua nếu đã có. Nếu đang
  đứng yên (không chạy dở), xóa luôn `runner.items`/`converter.items` (kết
  quả .done/.failed của đợt TRƯỚC) để `queueList` quay lại danh sách
  chỉnh sửa được — nếu không, `queueList` vẫn ưu tiên hiện snapshot cũ đó,
  khiến file mới tuy đã vào `queue` nhưng không hiện ra ở đâu cả (bug đã
  gặp: "chọn file mới thay file cũ không được").
- `pdfsInQueue` — computed, lọc `queue` chỉ còn file `.pdf` (dùng cho
  `convertButtons`/`canConvert`; `pdf_convert.py` mở file bằng PyMuPDF, không
  nhận `.docx`/`.pptx` làm input).
- `queueList` — hiện `runner.items`/`converter.items` (có trạng thái) khi
  đang chạy hoặc vừa chạy xong; hiện `queue` thô (xoá được từng file) khi
  rảnh. Gọi `queueItemsView(_:showCompare:showReveal:)` →
  `queueRow(_:showCompare:showReveal:)` → `statusIcon(_:)`.
- `queueRow(_:showCompare:showReveal:)` — 1 dòng file: icon trạng thái, tên
  file; nút "So sánh" (mở cả input/output bằng `NSWorkspace.shared.open` —
  dùng Preview.app có sẵn của macOS để xem trước bản dịch, không tự vẽ PDF)
  khi `showCompare` và item đã `.done` — chỉ bật cho `runner.items`; nút
  "Hiện trong Finder" (`activateFileViewerSelecting`) khi `showReveal` và
  item đã `.done` — chỉ bật cho `converter.items` (kết quả Word/PPTX); hoặc
  thông báo lỗi khi `.failed`.
- `settingsCard` — 2 ô key + Stepper số trang + dòng quota DeepL
  (`QuotaTracker.deeplUsage()`) + dòng quota Gemini
  (`QuotaTracker.geminiRequestsToday()`, hiện ngay khi có Gemini key, mặc
  định 0 nếu chưa từng fallback qua Gemini).
- `historyCard` — `DisclosureGroup` liệt kê `history.entries`, nút mở
  Finder từng dòng (`NSWorkspace.shared.activateFileViewerSelecting`), nút
  "Xóa lịch sử" → `history.clear()`.
- Gọi `runner.start(inputURLs: queue, ...)` khi bấm nút dịch (mọi định
  dạng trong hàng đợi); `converter.start(inputURLs: pdfsInQueue, ...)` khi
  bấm Xuất Word/PowerPoint (chỉ file PDF).

### ApiKeyValidator.swift
- `validateDeepL(_:)` — gọi `GET /v2/usage` (endpoint free/pro theo hậu tố
  `:fx`, giống `router.py::refresh_usage()`) — không tốn quota dịch thật.
- `validateGemini(_:)` — gọi `GET /v1beta/models?key=...` (liệt kê model,
  KHÔNG gọi `generateContent` — tránh tốn quota RPD chỉ để test key).
- `perform(_:)` (private) — dùng chung cho cả 2: map status code HTTP →
  `Outcome` (`.valid` nếu 200; 401/403 → "key sai/thu hồi"; 400 → "key
  không hợp lệ"; khác → hiện thẳng mã lỗi). Gọi bởi: `ContentView.testKey()`.

### KeychainStore.swift
- `service` = `"dev.cuonghoang.cpdfgear"` (cố định, độc lập với Bundle ID
  thật của process — dev qua `swift run` không có Info.plist nên không có
  Bundle ID ổn định để dựa vào).
- `load(_:)` — đọc Keychain qua `readKeychain(_:)`; nếu rỗng, di trú 1 lần
  từ UserDefaults (giá trị `@AppStorage` cũ trước khi chuyển sang Keychain,
  cùng tên key) — `save()` vào Keychain rồi `removeObject` khỏi
  UserDefaults, không còn lưu trùng ở 2 nơi. Gọi bởi:
  `ContentView.loadApiKeysFromKeychain()`.
- `save(_:value:)` — `value` rỗng thì `SecItemDelete`; khác rỗng thì thử
  `SecItemAdd`, nếu đã tồn tại (`errSecDuplicateItem`) thì `SecItemUpdate`
  thay vào đó. Gọi bởi: `load()` (di trú), `ContentView` qua `.onChange` khi
  người dùng sửa ô nhập key.
- `readKeychain(_:)` (private) — `SecItemCopyMatching`, trả `nil` nếu không
  tìm thấy/bị từ chối quyền (KHÔNG throw — người gọi tự fallback sang rỗng).

### UpdateChecker.swift (ObservableObject)
- `check()` — bỏ qua nếu KHÔNG có `CFBundleShortVersionString` trong
  Info.plist (tức đang chạy dev qua `swift run`, không có gì để so sánh).
  Gọi GitHub API `repos/cuonghm89/doctools/releases/latest`, so `tag_name`
  (bỏ tiền tố `v`) với version hiện tại qua `isNewer(_:than:)`; nếu mới hơn,
  set `latestVersion`/`releaseURL` (`@Published`, `ContentView` hiện
  `updateBanner`). Không tự tải/cài gì — chỉ báo, người dùng tự bấm "Tải
  về" mở trang Release. Gọi bởi: `ContentView.body` (`.onAppear`).
- `isNewer(_:than:)` (static, private) — so từng phần số `"x.y.z"`, phần
  thiếu coi là 0.

### QueueItem.swift
- `QueueItemStatus` (enum: pending/running/done(URL)/failed(String)),
  `QueueItem` (Identifiable: url + status). Dùng chung bởi
  `TranslationRunner` và `ConversionRunner` để mô hình hoá 1 hàng đợi file.

### HistoryStore.swift (ObservableObject)
- `HistoryEntry` (Codable) — kind, inputPath, outputPath, date.
- `add(kind:input:output:)` — thêm vào đầu `entries`, cắt bớt nếu vượt
  `maxEntries` (30), gọi `save()`. Gọi bởi: `ContentView` qua
  `runner.onItemDone`/`converter.onItemDone`.
- `clear()` — xoá hết, gọi `save()`.
- `load()`/`save()` (private) — đọc/ghi JSON vào `UserDefaults`
  (`translationHistory`).

### QuotaTracker.swift
- `deeplUsage()` — đọc lại `usage_tracker.json` mà
  `TranslationRouter.refresh_usage()` (PythonEngine/router.py) đã đồng bộ
  thẳng từ DeepL, trả về `(used, limit)` (nil nếu chưa dịch lần nào).
  `limit <= 0` nghĩa là tài khoản Pro không giới hạn cứng.
- `geminiRequestsToday()` — đọc lại `gemini_usage_tracker.json` (ghi bởi
  `router.py::_gemini()`), trả về số request Gemini đã gọi HÔM NAY (nil nếu
  chưa gọi lần nào). Chỉ là số đếm tham khảo (RPD thật do Google enforce,
  không tra được qua API key), không phải % quota chính xác.

### TranslationRunner.swift (ObservableObject)
- `TranslationConfig` (Encodable) — input_pdf, output_pdf, deepl_key,
  gemini_key, deepl_limit, max_pages.
- `start(inputURLs:deeplKey:geminiKey:maxPages:)` — nhận 1 hàng đợi file,
  set `items` (mỗi file 1 `QueueItem` trạng thái `.pending`), gọi
  `processNext()`.
- `processNext()` (private) — tăng `currentIndex`, dừng (set
  `isRunning = false`) khi hết hàng đợi; ngược lại build
  `TranslationConfig` cho file hiện tại (output = `<tên>_VN.<đuôi gốc>` —
  GIỮ NGUYÊN đuôi file input, để dịch .docx/.pptx ra đúng .docx/.pptx thay
  vì luôn ép thành .pdf), gọi
  `PythonProcess().run(scriptName: "translator_engine.py", ...)`. Trong
  `onFinish`, nếu item chưa được set `.done`/`.failed` bởi sự kiện JSON thì
  set `.failed` theo exit code/stderr, rồi tự gọi lại `processNext()` để xử
  lý file kế tiếp (đệ quy tuần tự cho tới hết hàng đợi).
- `applyEvent(_:)` (private, gọi từ closure `onEvent`) — parse JSON
  progress/done/error, cập nhật `@Published` state + `items[currentIndex]
  .status`; sự kiện "done" gọi `onItemDone?(input, output)`. Nhãn hiển thị
  theo `engine`: "deepl"/"gemini"/"skipped"/"code" (đoạn code — chỉ dịch
  chú thích, xem `code_blocks.py`) có nhãn tiếng Việt riêng, giá trị khác
  hiện nguyên văn.
- `cancel()` — gọi `PythonProcess.cancel()`, set item hiện tại `.failed`.

### ConversionRunner.swift (ObservableObject)
- `ConversionConfig` (Encodable) — input_pdf, output_docx?, output_pptx?.
- `start(inputURLs:toDocx:toPptx:)` — nhận 1 hàng đợi file, set `items`, gọi
  `processNext()`.
- `processNext()` (private) — cùng cơ chế tuần tự như
  `TranslationRunner.processNext()`; gom `output_docx`/`output_pptx` từ sự
  kiện "done" vào mảng `outputs` cục bộ, set item `.done`, gọi
  `onItemDone?(input, outputs)`, rồi tự gọi lại `processNext()`.
- `cancel()` — gọi `PythonProcess.cancel()`, set item hiện tại `.failed`.

### PythonProcess.swift (dùng chung bởi cả 2 runner ở trên)
- `engineDir` (static) — đường dẫn thư mục `PythonEngine`; ưu tiên
  `Bundle.main.resourceURL/PythonEngine` (app đã đóng gói qua
  `scripts/package_app.sh`), fallback suy ra project root từ `#filePath`
  lúc build (chạy dev qua `swift run`/Xcode). Không còn hardcode path theo
  máy/tài khoản cụ thể.
- `resolvedPythonPath` (static) — ưu tiên `Resources/PythonRuntime/bin/
  python3` (runtime Python riêng đóng gói sẵn deps qua `scripts/
  package_app.sh` — người dùng không cần cài gì), else `.venv/bin/python3`
  trong `PythonEngine/` (dev), else fallback `python3` từ `PATH`.
- `run<Config: Encodable>(scriptName:config:onEvent:onFinish:)` — ghi config
  ra file JSON tạm, spawn `Process` chạy `python3 <script> --config <file>`,
  đọc stdout từng dòng qua `parseEvent`, gọi callback `onEvent`/`onFinish`.
- `parseEvent(_:)` (static, private) — decode 1 dòng JSON thành `[String:
  Any]`.
- `cancel()` — `process?.terminate()`.

### Sources/ocr_cli/main.swift (binary riêng, KHÔNG thuộc app chính)
- Gọi bởi `ocr_pdf.py::_run_ocr()` qua subprocess, không phải bởi Swift app.
- `emitError(_:)` — in JSON lỗi ra stdout rồi `exit(1)`.
- `wordRanges(in:)` — tách 1 chuỗi CHỈ theo ký tự dấu cách thật (KHÔNG dùng
  `enumerateSubstrings(options: .byWords)` của Foundation — đã kiểm chứng
  THỰC TẾ nó tách cả ở dấu gạch nối, vd "on-demand" → "on"+"demand", và
  `candidate.boundingBox(for:)` trả về bbox SAI/TRÙNG LẶP cho 2 nửa từ ghép
  kiểu đó). Gọi bởi: script top-level.
- Script top-level: đọc ảnh từ `CommandLine.arguments[1]` → build
  `VNRecognizeTextRequest` (`.accurate`, dò ngôn ngữ tiếng Việt qua
  `supportedRecognitionLanguages()`) → `VNImageRequestHandler.perform()` →
  với mỗi observation, lấy bbox TỪNG TỪ (`candidate.boundingBox(for:)`,
  dùng `wordRanges(in:)`) và tự TÁCH observation đó thành nhiều "dòng" nếu
  khoảng cách ngang giữa 2 từ liên tiếp > 1.5x chiều cao dòng (dấu hiệu
  Vision đã gộp nhầm 2 CỘT khác nhau — cùng hàng ngang — thành 1 "dòng"
  duy nhất, vì ảnh scan không có ranh giới cột thật để nó biết dừng; cùng
  nguyên lý gap-based split đã dùng ở paragraphs.py::split_incoherent_block)
  → in JSON `{"lines": [...]}` (tọa độ đã quy đổi sang gốc trên-trái). Nếu
  không lấy được bbox từng từ (hiếm), rơi về dùng nguyên bbox cả observation
  như trước. LƯU Ý: cách tách này chỉ giúp khi 2 cột THẬT SỰ có khoảng
  trắng giữa chúng — khi cột bên trái có chữ dài chạm sát cột bên phải
  (không có khoảng trắng nào), phải dò đường kẻ lưới thay thế, xem
  `ocr_pdf.py::_detect_vertical_grid_lines()`.

---

## Python — PythonEngine

### translator_engine.py (pipeline DỊCH)
Entry point: `main()` (đọc `--config`, gọi `process_pdf()`).

- `emit(**event)` — in 1 dòng JSON ra stdout (giao thức tiến trình).
- `block_text(block)` — nối các dòng trong 1 khối PyMuPDF thành 1 chuỗi
  (chuẩn hóa `\xa0`→space). Gọi bởi: `group_translation_units()`.
- `local_bg_color(page, rect, pad=2)` — màu nền phổ biến nhất quanh 1 khối
  (dùng cho `add_redact_annot(fill=...)`). Dùng lại ở `pdf_convert.py`.
- `page_image_rects(page)` — rect các ảnh nhúng trên trang (loại ảnh full
  trang > 60% diện tích). Gọi bởi: `process_pdf()`.
- `intersects_image(rect, image_rects, threshold=0.15)` — Image Collision
  Guard, có chồng lấn ảnh thật không. Gọi bởi: `process_pdf()`.
- `growth_ceiling(rect, other_bboxes, page_bottom, margin=2)` — giới hạn
  giãn khung xuống dưới khi chữ dịch dài hơn khung gốc. Gọi bởi:
  `process_pdf()`.
- `_render_html(text, color, style, font_size)` — build HTML/CSS cho
  `insert_htmlbox`. Gọi bởi: `_try_insert()`.
- `_try_insert(page, rect, text, color, style, font_size)` — gọi
  `page.insert_htmlbox()`, trả về có vừa khung không. Gọi bởi:
  `fit_and_draw()`.
- `fit_and_draw(page, rect, text, base_font_size, color, style, max_y1=None)`
  — 3 tầng: (1) khung gốc scale 0.88, (2) giãn khung tới `max_y1`, (3)
  `_truncate_and_draw()`. Gọi bởi: `process_pdf()`.
- `_truncate_and_draw(page, rect, text, color, style, font_size)` — cắt bớt
  chữ + "…" khi 2 tầng trên đều thất bại. Gọi bởi: `fit_and_draw()`.
- `group_translation_units(group)` — 1 nhóm → list đơn vị dịch (tách riêng
  nếu là danh sách bullet). Gọi bởi: `build_translation_units()`.
- `build_translation_units(groups)` — gom đơn vị dịch của cả trang thành 1
  batch. Gọi bởi: `process_pdf()`. Gọi: `group_translation_units()`.
- `process_pdf(input_pdf, output_pdf, router, max_pages=0)` — hàm chính của
  pipeline dịch. Gọi: `ensure_text_layer()` (ocr_pdf.py),
  `merge_paragraph_blocks()` (paragraphs.py), `page_image_rects()`,
  `build_translation_units()`, `translate_units_with_code_awareness()`
  (code_blocks.py — thay cho gọi thẳng `router.translate_batch()`, xem
  CODEMAP mục code_blocks.py), `intersects_image()`, `growth_ceiling()`,
  `local_bg_color()`, `fit_and_draw()`. Gọi bởi: `main()`.
- `main()` — đọc `--config`, tạo `TranslationRouter`, TỰ NHẬN DIỆN định
  dạng theo đuôi file `input_pdf` (bất kể tên trường, chỉ là 1 path chuỗi):
  `.pdf` → `process_pdf()`; `.docx` → `translate_docx()`; `.pptx` →
  `translate_pptx()` (2 hàm sau import cục bộ từ `office_translate.py` bên
  trong `main()`, không phải ở đầu file — office_translate.py tự import
  ngược `emit` từ chính module này, import ở đầu file sẽ tạo vòng lặp
  import). Gọi bởi: chạy trực tiếp qua
  `python3 translator_engine.py --config ...` (từ `PythonProcess.swift`).

### office_translate.py (dịch trực tiếp .docx/.pptx, giữ nguyên định dạng)
- `FONT_SHRINK_MIN_SCALE` (0.55) / `FONT_SHRINK_MIN_SCALE_TABLE` (0.40) —
  chặn dưới khi co cỡ chữ; ô bảng có ngưỡng RIÊNG thấp hơn (chật hơn text
  box tự do, không có chỗ giãn dòng thoải mái). Xem `_adjust_font_for_length()`.
- `_METRIC_FONT` (`fitz.Font`, cùng file Arial dùng vẽ tiếng Việt trong
  translator_engine.py — `VN_FONT_DIR`), `_METRIC_FONT_SIZE` (100, cỡ đo
  cố định, tỉ lệ độ rộng tuyến tính theo fontsize nên không ảnh hưởng kết
  quả) — dùng cho `_text_width_ratio()`.
- `_text_width_ratio(original, translated)` — tỉ lệ ĐỘ RỘNG THẬT (đo bằng
  `fitz.Font.text_length()`, không đếm số ký tự) giữa bản dịch và bản gốc.
  Chính xác hơn đếm ký tự (vd "iiii" hẹp hơn nhiều "wwww" dù cùng 4 ký tự;
  bản HOA rộng hơn bản thường dù số ký tự bằng nhau). Chỉ đo 1 dòng, không
  mô phỏng xuống dòng nhiều dòng — vẫn là xấp xỉ so với fit_and_draw()
  (PDF) tự thử vẽ thật. Gọi bởi: `_adjust_font_for_length()`.
- `_apply_uppercase(original, translated)` — giữ chữ HOA gốc, y hệt logic
  trong `process_pdf()`.
- `_paragraph_text(paragraph)` — nối text mọi run trong 1 đoạn văn (docx
  `Paragraph` và pptx `_Paragraph` cùng có `.runs`, dùng chung được).
- `_adjust_font_for_length(run, original, translated, pt_class, min_scale)`
  — co cỡ chữ của run tỉ lệ nghịch với `_text_width_ratio()`, chặn dưới ở
  `min_scale` (0.55 text thường / 0.40 ô bảng, do caller truyền vào). Bỏ
  qua nếu `run.font.size` là `None` (kế thừa theo placeholder/theme, không
  có cỡ gốc để tính tỉ lệ co). Gọi bởi: `_write_paragraph()`.
- `_write_paragraph(paragraph, original, translated, pt_class, min_scale)`
  — ghi cả bản dịch vào RUN ĐẦU TIÊN (giữ định dạng run đó cho toàn đoạn)
  rồi gọi `_adjust_font_for_length()` trên chính run đó; xóa rỗng các run
  còn lại — 1 đoạn văn có thể bị chia nhiều run (đổi định dạng giữa câu,
  hoặc Word/PowerPoint tự tách không rõ lý do); dịch từng run riêng sẽ mất
  ngữ cảnh.
- `_translate_paragraphs(paragraph_specs, router, page, total, pt_class)`
  — dùng chung cho cả docx/pptx. `paragraph_specs`: list `(paragraph,
  in_table)` — `in_table` ở ĐÂY chỉ để CHỌN `min_scale` (KHÔNG phải để
  định tuyến DeepL/Gemini như `is_table` của PDF pipeline; mọi đoạn văn dù
  trong ô bảng hay không đều gửi router với `is_table=False` như nhau, vì
  bảng .docx/.pptx là bảng THẬT, không cần heuristic `is_table_lines()`).
  Gộp text mọi đoạn thành 1 batch, gọi
  `translate_units_with_code_awareness(units, router,
  enforce_length_guard=False)` (code_blocks.py — thay cho gọi thẳng
  `router.translate_batch()`, xem CODEMAP mục code_blocks.py) 1 lần — TẮT
  guard "phình quá dài thì giữ bản gốc" của router (guard đó chỉ đúng cho
  PDF, khung pixel cố định không co giãn được; docx/pptx tự co chữ ở đây
  thay vì bỏ dịch, bật guard sẽ tạo tiêu đề/đoạn văn nửa Anh nửa Việt) —
  ghi lại từng đoạn qua `_write_paragraph()`, `emit()` progress mỗi đoạn
  dịch được. Gọi bởi: `translate_docx()`, `translate_pptx()`.
- `_iter_docx_table_paragraphs(table)` — đệ quy vào bảng con trong 1 ô,
  trả về `(paragraph, True)`.
- `_iter_docx_paragraphs(doc)` — mọi đoạn văn cần dịch: body, mọi bảng
  (đệ quy), header/footer mỗi section — trả về `(paragraph, in_table)`.
  Gọi bởi: `translate_docx()`.
- `translate_docx(input_path, output_path, router, max_pages=0)` — hàm
  chính dịch .docx. `max_pages` BỊ BỎ QUA (.docx không có khái niệm trang
  cố định trong XML). Gọi: `_iter_docx_paragraphs()`,
  `_translate_paragraphs(..., pt_class=docx.shared.Pt)`. Gọi bởi: `main()`
  (translator_engine.py).
- `_iter_pptx_text_frames(shapes)` — đệ quy vào group shape; bảng thì lấy
  text_frame từng cell (không phải chính shape) — trả về `(text_frame,
  is_table_cell)`.
- `EMU_PER_POINT` (12700, hằng số chuyển đổi chuẩn OOXML), `LINE_HEIGHT_FACTOR`
  (1.2), `DEFAULT_CELL_MARGIN_LR_EMU`/`DEFAULT_CELL_MARGIN_TB_EMU` — dùng
  cho `_wrapped_line_count()`/`_grow_table_rows_to_fit()`.
- `_wrapped_line_count(text, font_size_pt, max_width_pt)` — số dòng cần để
  word-wrap `text` vừa `max_width_pt`, đo bằng CHÍNH `_METRIC_FONT` (word-
  wrap THAM LAM: xếp từ vào dòng hiện tại tới khi không vừa thì xuống dòng
  mới). Gọi bởi: `_grow_table_rows_to_fit()`.
- `_grow_table_rows_to_fit(table)` — LỚP PHÒNG THỦ THỨ 2 sau co chữ: co chữ
  có chặn dưới (`FONT_SHRINK_MIN_SCALE_TABLE`) nên KHÔNG đảm bảo hết tràn
  nếu bản dịch phình quá nhiều; hàm này đo số dòng THẬT SỰ cần (sau khi đã
  co chữ) bằng `_wrapped_line_count()` trên độ rộng cột thật (trừ margin
  trái/phải của cell), rồi GIÃN `row.height` cho đủ chỗ — chỉ giãn, không
  bao giờ co nhỏ lại. Đây là cách DUY NHẤT đảm bảo hết tràn dọc (co chữ chỉ
  giảm khả năng tràn, không đảm bảo tuyệt đối). Gọi bởi: `translate_pptx()`.
- `_iter_pptx_tables(shapes)` — đệ quy vào group shape, trả về mọi bảng
  thật trên 1 tập shape. Gọi bởi: `translate_pptx()`.
- `translate_pptx(input_path, output_path, router, max_pages=0)` — hàm
  chính dịch .pptx, theo TỪNG SLIDE (1 slide = 1 "trang" thật, khác docx);
  `max_pages > 0` chỉ dịch N slide đầu, các slide sau giữ nguyên. Trước khi
  dịch, với mọi text_frame trên slide: ép `word_wrap = True` (tránh tràn
  NGANG sang cột/shape bên cạnh, kể cả khi file gốc tắt wrap — áp cho cả
  text box lẫn ô bảng); riêng text box (không phải ô bảng) còn set
  `auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE` — bật tính năng tự co chữ
  CÓ SẴN của PowerPoint (normAutofit), PowerPoint thật tự tính lại
  fontScale chính xác hơn `_adjust_font_for_length()` khi mở file, dùng
  làm lớp phòng thủ thứ 2. KHÔNG bật `auto_size` cho ô bảng (PowerPoint tự
  quản lý layout bảng riêng, normAutofit trên 1 cell không đáng tin cậy
  tương đương text box) — ô bảng dùng `_grow_table_rows_to_fit()` thay
  thế, gọi SAU khi `_translate_paragraphs()` xong (cần đọc lại cỡ chữ ĐÃ CO
  của run để tính đúng số dòng còn cần). Gọi: `_iter_pptx_text_frames()`,
  `_translate_paragraphs(..., pt_class=pptx.util.Pt)`,
  `_iter_pptx_tables()`, `_grow_table_rows_to_fit()`. Gọi bởi: `main()`
  (translator_engine.py).

### code_blocks.py (nhận diện đoạn code, chỉ dịch chú thích)
Dùng chung bởi cả `translator_engine.py` (PDF) và `office_translate.py`
(.docx/.pptx) — theo yêu cầu: đoạn mã nguồn trong tài liệu KHÔNG được dịch,
chỉ chú thích trong code mới dịch.

- `_is_code_line(line)` — 1 dòng có "trông giống code" không, dựa trên tổ
  hợp: từ khoá code (`_CODE_KEYWORDS` — CỐ Ý loại `if`/`for`/`while`/
  `return`/`import`/... vì đây là từ tiếng Anh thường mở đầu câu văn xuôi
  thật, dùng riêng sẽ nhận nhầm; từ khoá 1 mình cần thêm dấu `(` hoặc kết
  thúc bằng `:` mới tính), câu lệnh `import`/`from...import`
  (`_CODE_IMPORT_RE`, khá đặc trưng nên tự nó đủ), 1 dòng chỉ gồm lệnh gọi
  hàm (`_CODE_CALL_RE`) hoặc phép gán đơn giản (`_CODE_ASSIGN_RE`), kết
  thúc bằng `;{}` (`_CODE_LINE_ENDING_RE`), hoặc cú pháp đặc trưng `=>`/
  `->`/`::`/`#include`/`<?php` (`_CODE_LINE_SYNTAX_RE`, tự nó đủ).
- `is_code_snippet(text)` — tỉ lệ dòng "giống code" (qua `_is_code_line`)
  trên tổng số dòng không rỗng ≥ `CODE_LINE_RATIO_THRESHOLD` (0.5). Gọi
  bởi: `translate_units_with_code_awareness()`.
- `_find_comment_marker(line)` — vị trí marker comment đầu tiên (`#`, `//`,
  `--`) trên 1 dòng, bỏ qua marker nằm trong chuỗi ký tự (kiểm tra thô: số
  dấu nháy `"`/`'` trước đó phải chẵn — `_quote_parity_ok()`). KHÔNG xử lý
  comment khối nhiều dòng (`/* ... */` tràn dòng, `<!-- -->`) — dòng đó giữ
  nguyên, không dịch.
- `split_code_comments(text)` — tách `text` thành `(template, [comment_texts])`;
  `template` giữ nguyên 100% phần code, thay phần chú thích bằng placeholder
  `\x00{i}\x00`. Gọi bởi: `translate_units_with_code_awareness()`.
- `reassemble_code_comments(template, translated_comments)` — thay từng
  placeholder bằng chú thích đã dịch (dùng `.replace()`, KHÔNG dùng
  `.format()` trên cả `template` — code thật có thể chứa `{`/`}` literal,
  `.format()` sẽ vỡ). Gọi bởi: `translate_units_with_code_awareness()`.
- `translate_units_with_code_awareness(units, router, **translate_kwargs)`
  — wrapper quanh `router.translate_batch()`, CÙNG SHAPE input/output (list
  `(text, is_table)` → list `(bản_dịch, engine, deepl_error, item_error)`,
  không đổi gì ở phía gọi ngoài việc đổi tên hàm). Unit nào `is_code_snippet`
  thì tách riêng: mọi chú thích của MỌI unit code gộp thành 1 batch dịch
  riêng (`comment_units`), phần code không bao giờ gửi qua DeepL/Gemini.
  Unit code KHÔNG có chú thích nào thì trả thẳng `(text_gốc, "skip", None,
  None)` — không tốn quota. Unit dịch được gắn `engine="code"` (UI hiển thị
  riêng, xem `TranslationRunner.swift`). Gọi bởi: `process_pdf()`
  (translator_engine.py), `_translate_paragraphs()` (office_translate.py).

### router.py (gọi API dịch)
- `_session_with_retries()` — `requests.Session` có `Retry` (429/5xx).
- `load_tracker()`, `save_tracker()` — cache local `{chars_used, limit}` của
  lần đồng bộ gần nhất với DeepL. KHÔNG tự đoán ngày reset (DeepL free tier
  reset theo ngày đăng ký, vd 29 hàng tháng, không phải đầu tháng dương
  lịch — tự đếm+tự reset theo tháng luôn lệch ngày). Chỉ dùng khi
  `refresh_usage()` gọi mạng thất bại.
- `_current_day()`, `load_gemini_tracker()`, `save_gemini_tracker()` — đếm
  số request Gemini theo NGÀY (Gemini free tier tính quota theo request/
  ngày - RPD, khác hẳn DeepL tính theo ký tự/tháng), file JSON riêng
  (`gemini_usage_tracker.json`). Đọc lại từ Swift qua
  `QuotaTracker.geminiRequestsToday()`.
- `is_table_lines(lines)` / `is_table_block(block)` — heuristic "trông giống
  bảng" (nhiều dòng, nhiều chữ số). Dùng ở `translator_engine.py` và test.
- `_looks_like_acronym_or_code(text)`, `_translation_expanded_too_much(...)`
  — guard không dịch/không nhận bản dịch phình quá mức cho từ viết tắt.
  Gọi bởi: `TranslationRouter.translate()`/`translate_batch()`.
- `class TranslationRouter`:
  - `__init__(deepl_key, gemini_key, deepl_limit=...)` — load cả
    `self.tracker` (DeepL, từ cache local) và `self.gemini_tracker`
    (Gemini). Không gọi mạng.
  - `refresh_usage()` — đồng bộ `self.tracker`/`self.deepl_limit` với số
    liệu THẬT từ DeepL (`GET /v2/usage`, dùng `self.session`). `limit == 0`
    trong response nghĩa là tài khoản Pro không giới hạn cứng. Lỗi mạng/key
    sai thì im lặng giữ nguyên cache cũ (không chặn dịch). Gọi bởi:
    `main()` (translator_engine.py), 1 lần duy nhất khi bắt đầu 1 lần dịch
    — không gọi trong `__init__` để giữ test không cần mạng.
  - `translate(text, is_table=False, enforce_length_guard=True)` — dịch 1
    đoạn (DeepL → fallback Gemini); `can_use_deepl` coi `self.deepl_limit
    <= 0` là "không giới hạn". `enforce_length_guard=False`: bỏ qua
    `_translation_expanded_too_much()` — guard đó chỉ đúng cho PDF (khung
    pixel cố định, không co giãn được); `office_translate.py` tự co cỡ
    chữ thay vì bỏ dịch nên luôn gọi với `False`. Gọi: `_deepl()`,
    `_gemini()`.
  - `translate_batch(items, enforce_length_guard=True)` — dịch cả batch
    (dùng `_deepl_batch()` khi có thể); tham số `enforce_length_guard`
    truyền xuống mọi lời gọi `translate()` bên trong (đường dẫn per-item
    fallback). Gọi bởi: `process_pdf()` (translator_engine.py, giữ mặc
    định `True`), `office_translate.py::_translate_paragraphs()` (luôn
    `False`).
  - `_deepl_batch(texts)`, `_deepl(text)` — gọi HTTP API DeepL thật.
  - `_gemini(text)` — gọi HTTP API Gemini thật; đây là chỗ DUY NHẤT mọi lời
    gọi Gemini đi qua (kể cả từ `translate()` lẫn nhánh fallback trong
    `translate_batch()`), nên cũng là chỗ duy nhất tăng
    `self.gemini_tracker["requests_used"]` — tăng ngay sau khi có response
    (kể cả response lỗi 429/5xx/bị chặn vẫn tính, vì Google đã nhận request
    đó vào quota RPD rồi), trước khi `raise_for_status()`.

### paragraphs.py (gộp/tách khối chữ thành đoạn văn)
- `is_bullet_text(text)` — có phải dòng bullet/đánh số không (chỉ nhận ký
  tự bullet THẬT trong chuỗi — xem `_marker_prefix_content_x0()` cho
  trường hợp bullet không có ký tự thật).
- `block_font_style(block)` — (bold, italic) từ span đầu.
- `_avg_font_size(block)` — cỡ chữ trung bình 1 khối.
- `block_text_color(block)` — màu span đầu, tuple int 0-255.
- `_marker_prefix_content_x0(block)` — phát hiện bullet dạng "marker hoán
  đổi glyph": 1 số PDF xuất từ Word/PowerPoint vẽ bullet bằng font biểu
  tượng (Wingdings-kiểu, vd mũi tên ➡) nhưng ToUnicode/`get_text()` trả về
  span ĐẦU là 1 dấu cách trắng (không có ký tự bullet thật) ở font KHÁC
  hẳn nội dung theo sau — `is_bullet_text()` không nhận ra được, và x0 của
  CẢ BLOCK bị kéo lệch về vị trí marker thay vì vị trí chữ thật, khiến MỌI
  bullet trong 1 danh sách trông như "cùng cột" → `merge_paragraph_blocks`
  gộp nhầm các mục danh sách RIÊNG BIỆT thành 1 (bug thật, root-cause từ
  file PDF thật của user: các bullet "Load Testing/Stress Testing/..."
  gộp thành 1 khối, dịch dính cục, mũi tên bullet mồ côi không có chữ).
  Trả về x0 của span chữ thật đầu tiên (bỏ qua marker) nếu phát hiện được,
  else `None`. Gọi bởi: `merge_paragraph_blocks()`.
- `split_incoherent_block(block, x_tolerance=3, gap_factor=1.3)` — tách 1
  "block" PyMuPDF tự gộp nhầm nhiều dòng KHÔNG liên quan (khác cột HOẶC
  cách xa nhau theo Y) thành nhiều block con. Gọi bởi:
  `merge_paragraph_blocks()`, `pdf_convert.py::_page_lines()`.
- `merge_paragraph_blocks(blocks, x_tolerance=3, gap_factor=1.3,
  size_tolerance=0.25)` — gộp các block cùng cột/cỡ/màu/style, cách nhau đủ
  gần, thành 1 "group" = 1 đoạn văn/bullet. So cột (`same_column`) dùng
  `anchor_x0` của group (= content x0, bỏ qua marker nếu group được mở đầu
  bởi 1 block có marker) thay vì x0 thô — nhờ vậy dòng word-wrap tiếp theo
  của 1 bullet-marker (căn theo lề chữ thật) vẫn gộp đúng vào nhóm đó.
  Block có marker LUÔN mở nhóm MỚI, không bao giờ gộp vào nhóm trước (tự
  nó là điểm bắt đầu 1 mục danh sách, bất kể x0/style/color thô trùng
  nhóm nào). Gọi: `split_incoherent_block()`, `_marker_prefix_content_x0()`.
  Gọi bởi: `process_pdf()`, `pdf_convert.py::convert_to_pptx()`.

### ocr_pdf.py (OCR trang scan bằng Vision framework)
- `_default_ocr_binary()` — suy ra đường dẫn `ocr_cli`: ưu tiên
  `Contents/MacOS/ocr_cli` cạnh app đã đóng gói (`scripts/package_app.sh`),
  fallback `<project root>/.build/debug/ocr_cli` (suy từ `__file__`) khi
  chạy dev. Gán vào hằng `DEFAULT_OCR_BINARY` (module-level, chạy 1 lần lúc
  import) — không còn hardcode path theo máy cụ thể.
- `_estimate_font_size(text, width, height)` — ước lượng cỡ chữ ưu tiên
  theo chiều rộng thật (đo bằng `fitz.Font.text_length`), fallback chiều
  cao. Gọi bởi: `ocr_page()`.
- `_page_has_real_text(page)` — trang đã có ≥20 ký tự chữ thật chưa. Gọi
  bởi: `ocr_page()`.
- `_detect_ink_color(page, rect)` — dò màu mực thật bằng 2-means trên pixel
  (xử lý được cả nền gradient). Gọi bởi: `ocr_page()`.
- `_run_ocr(image_path, ocr_binary)` — subprocess gọi `ocr_cli` (Swift),
  parse JSON kết quả. Gọi bởi: `_ocr_pixmap()`.
- `_ocr_pixmap(pix, ocr_binary)` — ghi `pix` ra file PNG tạm rồi gọi
  `_run_ocr()`, tự dọn file tạm. Gọi bởi: `ocr_page()`.
- `GRID_LINE_*` (hằng số ngưỡng), `_detect_vertical_grid_lines(pix)` — dò
  đường kẻ dọc THẬT (viền cột bảng) trong ảnh cả trang: cột có độ sáng
  trung bình (`col_mean`) THẤP TUYỆT ĐỐI (< `GRID_LINE_MAX_MEAN`) VÀ thấp
  hơn hẳn 2 bên (> `GRID_LINE_MIN_CONTRAST`) — đã kiểm chứng trên dữ liệu
  thật: đường kẻ lưới thật có mean rất thấp (~26-140), cột nền trắng có/
  không chữ đi qua luôn có mean cao (~200+) dù ĐỘ LỆCH CHUẨN của nó dao
  động thất thường (KHÔNG dùng std làm tín hiệu — thử rồi, loại nhầm 1
  đường kẻ thật vì std hơi cao). Trả về list vị trí x (pixel), rỗng nếu
  trang không có đường kẻ rõ (bỏ qua 2 viền ngoài cùng trang — không có gì
  để tách ở đó). Gọi bởi: `ocr_page()`.
- `ocr_page(page, ocr_binary, dpi_scale=2.0, visible=False)` — OCR 1 trang,
  chèn lớp chữ (ẩn hoặc hiện) đúng vị trí/màu/cỡ, tự phát hiện & xử lý nhãn
  xoay dọc (rotate=90). NẾU `_detect_vertical_grid_lines()` tìm được đường
  kẻ: cắt riêng từng DẢI CỘT (chỉ cắt theo trục X, giữ nguyên chiều cao cả
  trang — nên y/height của mỗi dòng OCR ra đã đúng thang page_h sẵn, không
  cần quy đổi lại) và OCR RIÊNG TỪNG DẢI qua `_ocr_pixmap()`, rồi merge lại
  (cộng offset + quy đổi x/width theo bề rộng dải) — Vision không bao giờ
  nhìn thấy 2 cột cùng lúc nữa nên không thể gộp nhầm chữ 2 cột thành 1
  dòng (bug thật đã gặp: ô "Advantages" có chữ dài gần chạm cột "Remarks"
  bên cạnh, Vision tự gộp cả 2 thành 1 "dòng" duy nhất khi OCR cả trang 1
  lần). KHÔNG có đường kẻ: OCR nguyên trang 1 lần như cũ. Trả về list
  `(rect_gốc, ink_color)`. Gọi: `_page_has_real_text()`,
  `_detect_vertical_grid_lines()`, `_ocr_pixmap()`, `_estimate_font_size()`,
  `_detect_ink_color()`. Gọi bởi: `ensure_text_layer()`.
- `ensure_text_layer(doc, ocr_binary=DEFAULT_OCR_BINARY, visible=False)` —
  OCR mọi trang chưa có chữ thật trong `doc`. Trả về `{trang: [(rect,
  color), ...]}`. Gọi bởi: `process_pdf()` (translator_engine.py),
  `convert_to_docx()`/`convert_to_pptx()` (pdf_convert.py).

### pdf_convert.py (xuất Word/PowerPoint, độc lập với pipeline dịch)
- `convert_to_docx(input_pdf, output_docx)` — OCR (visible=True) vào file
  tạm, giao hẳn cho thư viện `pdf2docx` dựng file .docx. Gọi:
  `ensure_text_layer()`.
- `_page_background_png(bg_doc, page_index, ocr_rects_by_page, image_rects, table_bbox=None)`
  — render 1 trang thành ảnh nền sau khi xóa đúng pixel chữ cũ (dùng
  `local_bg_color()` để tô lại đúng màu nền, không phải trắng phẳng) VÀ
  pixel của `image_rects` (ảnh rời rạc — sẽ được vẽ lại riêng, xem
  `_crop_image_png()`), qua 1 lượt `apply_redactions()` RIÊNG (images=
  PIXELS) tách khỏi lượt xóa chữ, để không nới lỏng an toàn images=NONE
  của lượt xóa chữ trên trang số hóa. `table_bbox` (khi trang này sẽ dựng
  bảng PowerPoint thật — `_add_table_shape()`): VẼ ĐÈ (`page.draw_rect()`,
  KHÔNG dùng `add_redact_annot`/`apply_redactions(graphics=...)`) 1 hình
  chữ nhật trắng đúng vùng đó, để dọn nền/vạch kẻ VECTOR THẬT của bảng gốc
  (redaction chữ/ảnh ở trên không đụng tới vector) — nếu không, bảng
  PowerPoint dựng lại sẽ đè lên 1 bảng gốc vẫn còn nguyên bên dưới, nhìn
  như "2 bảng chồng nhau". Cố tình KHÔNG dùng
  `graphics=PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED` — đã thử, có 2 bug: (1)
  xóa NGUYÊN CẢ path vector chạm vào rect chứ không chỉ phần nằm trong
  rect (1 nền xám của bảng gốc thường rộng hơn hẳn `table_bbox`, làm mất
  màu nền đúng của phần không thuộc bảng); (2) tô lại bằng
  `local_bg_color(page, table_bbox)` tự đọc lại CHÍNH vùng đang xóa lúc
  chữ đã mất nhưng nền xám vector thì chưa, vô tình tô lại y hệt màu xám
  cũ. `page.draw_rect()` (vẽ đè, không redact) tránh cả 2, chỉ phủ ĐÚNG
  diện tích `table_bbox` bằng trắng cố định. Gọi bởi: `convert_to_pptx()`.
- `_page_image_rects_for_export(page, is_ocr_page)` — mọi ảnh nhúng rời
  rạc trên trang không-scan, KỂ CẢ ảnh lớn (không áp ngưỡng >60% diện tích
  như `translator_engine.page_image_rects()` — ngưỡng đó dành cho Image
  Collision Guard lúc dịch, chỉ cần bảo vệ logo/icon nhỏ; áp nhầm vào export
  khiến ảnh lớn/minh họa bị coi nhầm là "ảnh nền toàn trang" và không được
  crop riêng). Trang scan trả về `[]` (ảnh CHÍNH LÀ nội dung cả trang,
  không có ảnh rời rạc nào để crop). Gọi bởi: `convert_to_pptx()`.
- `_crop_image_png(page, rect, zoom=2.0)` — render đúng 1 vùng ảnh từ trang
  GỐC (trước khi bị `_page_background_png()` xóa), để chèn thành 1 picture
  shape riêng, sửa được (di chuyển/đổi cỡ/xóa độc lập) thay vì bị khoá cứng
  vào ảnh nền toàn trang. Gọi bởi: `convert_to_pptx()`.
- `_add_text_box(slide, group, page_height, ocr_rects=None, scale=1.0)` —
  1 nhóm (đã gộp đoạn văn) → 1 text box PowerPoint, dùng chung 1 cỡ chữ; tự
  xoay khối nếu nhãn xoay dọc (`line["dir"]`); màu chữ lấy qua
  `_line_color(line, ocr_rects)` (không đọc thẳng `span["color"]` — luôn
  đen hardcode với chữ OCR, xem `_line_color`). Gọi bởi: `convert_to_pptx()`.
- `_cluster_1d(items, gap)` — gom điểm 1 chiều thành các dải liên tục. Gọi
  bởi: `_detect_table()`.
- `_label_continues_in_other_columns(lines, y0, y1, label_col_x_max, tolerance=3.0)`
  — có dòng nào ở CÁC CỘT KHÁC bắt đầu trong [y0-tol, y1+tol] và mở đầu
  bằng chữ thường không (dấu hiệu câu đang viết dở, cùng 1 hàng bị nhãn
  wrap) — tín hiệu phụ cho `_merge_wrapped_row_labels()`, vì riêng khoảng
  cách dòng nhãn không đủ phân biệt "nhãn wrap" với "2 hàng thật đứng sát
  nhau" (đã đo thực tế: cả 2 ca đều chỉ cách nhau ~0.3-1.2pt). CHÚ Ý: so
  sánh `bbox[0] < label_col_x_max` (không phải `<=`) — bug thật đã gặp khi
  tọa độ tròn số trùng khớp ranh giới, `<=` loại nhầm luôn cột dữ liệu.
- `_merge_wrapped_row_labels(bboxes_sorted_by_y, lines, label_col_x_max, gap_factor=1.0)`
  — gộp 2 dòng nhãn liên tiếp thành 1 neo hàng khi khoảng cách NHỎ (dưới
  gap_factor lần chiều cao dòng trước) VÀ có xác nhận từ
  `_label_continues_in_other_columns()`. Gọi bởi: `_detect_table()`.
- `_detect_table(lines)` — thử nhận diện 1 bảng từ `lines` (dòng gốc, chưa
  gộp đoạn văn): dò cột nhãn (thử lần lượt từng cột trái→phải), lọc theo độ
  dài nhãn/cỡ chữ/tỉ lệ số dòng nhãn-so-dữ liệu, gộp nhãn wrap
  (`_merge_wrapped_row_labels`), tỉ lệ lấp đầy ô (`TABLE_MIN_FILL_RATIO`),
  2 cột đầu không được trùng nhau. TỪ CHỐI hẳn (trả `None`) nếu bất kỳ ô
  nào cần nhiều dòng hơn `TABLE_MAX_CELL_LINES` (4) — bảng càng dày đặc
  (đoạn văn/gạch đầu dòng dài) càng dễ khiến 1 dòng LẠC (tiêu đề nhóm,
  không phải nhãn hàng thật — bug thật đã gặp) lọt qua mọi kiểm tra trên
  và kéo lệch toàn bộ hàng phía sau; không tìm được tín hiệu nào tách ca
  đó khỏi bảng thật (bố cục tài liệu có thể rất tự do, nhãn hàng thật
  cũng có khi lệch xa nội dung cột bên) — AN TOÀN HƠN là từ chối dựng
  bảng, để nội dung vẫn vẽ ĐÚNG VỊ TRÍ THẬT qua text box thường
  (`convert_to_pptx()` tự làm việc này khi hàm này trả `None`) thay vì có
  nguy cơ lệch hàng trong 1 bảng "trông như đúng". Gọi: `_cluster_1d()`,
  `_merge_wrapped_row_labels()`, `_trim_row_anchor_outliers()`,
  `_build_table_cells()`. Gọi bởi: `convert_to_pptx()`.
- `_trim_row_anchor_outliers(bboxes_sorted_by_y, outlier_factor=2.2)` — cắt
  ứng viên neo hàng lạc lõng ở đầu/cuối dãy. Gọi bởi: `_detect_table()`.
- `_interval_index(value, anchors, tolerance=0.0)` — chỉ số neo LỚN NHẤT
  không thấp hơn `value` quá `tolerance` (khoảng [anchors[i], anchors[i+1])
  mà `value` rơi vào) — KHÔNG phải neo gần nhất theo khoảng cách tuyệt
  đối. Dùng cho CẢ gán hàng lẫn gán cột trong `_build_table_cells()`: 1
  hàng/cột có thể rộng/cao hơn hẳn hàng/cột lân cận (ô wrap nhiều dòng
  hơn nhãn ngắn của chính nó; hoặc tiêu đề canh GIỮA nằm xa mép trái cột
  rộng — cả 2 đều gặp thật) khiến nội dung ở rìa xa lại GẦN neo BÊN CẠNH
  hơn theo khoảng cách tuyệt đối dù vẫn thuộc hàng/cột hiện tại — "gần
  nhất" gán sai, "khoảng [neo hiện tại, neo sau)" luôn đúng. Gọi bởi:
  `_build_table_cells()`.
- `_line_color(line, ocr_rects)` — màu 1 dòng (khớp `ocr_rects` nếu trang
  OCR, không thì đọc `span["color"]`). Gọi bởi: `_page_lines()`.
- `_page_lines(page, ocr_rects=None)` — mọi dòng chữ của trang (đã tách
  block gộp nhầm qua `split_incoherent_block()`, loại nhãn xoay dọc). Gọi
  bởi: `convert_to_pptx()`.
- `_build_table_cells(lines, row_y, col_x)` — gán mỗi dòng vào (hàng, cột)
  theo `_interval_index()` cho CẢ 2 trục (xem docstring ở đó — không dùng
  neo gần nhất cho trục nào), loại dòng nằm hẳn ngoài phạm vi hàng/cột
  đầu/cuối của bảng. Gọi bởi: `_detect_table()`, `convert_to_pptx()`.
- `_table_crosses_section_divider(page, table_bbox_rect, min_width_ratio=0.9, min_height=5.0)`
  — True nếu có 1 thanh/khối trang trí gần như CẢ CHIỀU RỘNG TRANG VÀ đủ
  cao (loại vạch kẻ mỏng bình thường giữa các dòng, ~0.5pt, không phải
  ranh giới nhóm) cắt ngang qua vùng dọc của bảng — dấu hiệu bảng đang gộp
  nhầm nội dung 2 NHÓM khác nhau (ngăn cách bằng 1 thanh tiêu đề nhóm, vd
  nền xám đậm "Giải pháp X") thành 1 bảng liên tục, vì `_detect_table()`
  chỉ dò theo 1 cột nhãn hẹp, không biết ranh giới này (bug thật đã gặp: 1
  bảng nhỏ 2 cột vô tình trải dài qua đúng 1 thanh ngăn cách, kéo theo cả
  hàng thuộc nhóm KHÁC). An toàn hơn là từ chối hẳn bảng đó (như
  `TABLE_MAX_CELL_LINES`) thay vì cố tách lại đúng ranh giới. Gọi bởi:
  `convert_to_pptx()`.
- `_table_bbox_rect(table_info, cells)` — vùng hình chữ nhật (left, top,
  width, height, point) mà bảng PowerPoint sẽ chiếm — trích riêng khỏi
  `_add_table_shape()` để `convert_to_pptx()` gọi được TRƯỚC khi vẽ ảnh
  nền (cần biết trước để xóa vạch kẻ/nền vector gốc trong đúng vùng đó,
  xem `_page_background_png()`), tránh tính 2 công thức có thể lệch nhau.
  Trả về `None` nếu `cells` rỗng. Gọi bởi: `_add_table_shape()`,
  `convert_to_pptx()`.
- `_add_table_shape(slide, table_info, cells)` — dựng bảng PowerPoint thật
  (`slide.shapes.add_table`, vị trí/kích thước từ `_table_bbox_rect()`),
  set độ rộng/cao TỪNG cột/hàng, màu/cỡ chữ từng ô. `col_edges`/`row_edges`
  PHẢI bắt đầu từ `left`/`top` (biên thật của bảng), KHÔNG phải
  `col_x[0]`/`row_y[0]` — bug thật đã gặp: `left = col_x[0] - 4` (đệm 4pt
  sang trái/trên so với neo cột/hàng đầu), bắt đầu từ `col_x[0]` làm 4pt
  đệm đó không được cộng vào cột/hàng đầu tiên nào cả — TỔNG độ rộng/cao
  thật (python-pptx tự tính lại `shape.width`/`height` bằng tổng từng cột/
  hàng khi set riêng) hụt mất đúng 4pt so với `_table_bbox_rect()` đã dùng
  để xóa nền (`_page_background_png(table_bbox=...)`), để lại viền hở lộ
  nền/vạch kẻ gốc chưa xóa — nhìn như "bảng trong bảng" dù nền đã xóa
  đúng vùng. Gọi bởi: `convert_to_pptx()`.
- `convert_to_pptx(input_pdf, output_pptx)` — hàm chính xuất PPTX. Mỗi
  trang: tính `image_rects` + `lines`/`table_info`/`table_cells` TRƯỚC
  (cần biết có dựng bảng hay không, và dựng ở đâu, trước khi vẽ ảnh nền);
  nếu `table_cells` khác rỗng nhưng bbox của nó cắt ngang qua 1 thanh chia
  nhóm (`_table_crosses_section_divider()`), HỦY bảng (đặt lại `None`) —
  fallback về text box thường cho toàn trang → vẽ ảnh nền (đã xóa chữ,
  `image_rects`, VÀ vùng bảng nếu có — xem `_page_background_png()`) → vẽ
  từng ảnh trong `image_rects` thành picture shape riêng đúng vị trí
  (`_crop_image_png()`) → dựng bảng (`_add_table_shape()`, SAU ảnh nền để
  đúng thứ tự z — bảng nằm trên) → text box đè lên trên (bỏ qua nhóm đã
  nằm trong bbox bảng). Gọi: `ensure_text_layer()`,
  `_page_image_rects_for_export()`, `merge_paragraph_blocks()`,
  `_page_lines()`, `_detect_table()`, `_build_table_cells()`,
  `_table_bbox_rect()`, `_table_crosses_section_divider()`,
  `_page_background_png()`, `_crop_image_png()`, `_add_table_shape()`,
  `_add_text_box()`.

### convert_cli.py (CLI wrapper, entry point cho Swift subprocess)
- `emit(**event)` — in JSON progress/done/error ra stdout.
- `main()` — đọc `--config`, gọi `convert_to_docx()`/`convert_to_pptx()`
  tùy theo trường nào có trong config. Gọi bởi: chạy trực tiếp qua
  `python3 convert_cli.py --config ...` (từ `PythonProcess.swift`).

### test_*.py (self-check, chạy trực tiếp `python3 test_X.py`)
- `test_router.py` — stub HTTP, test `TranslationRouter`/`translate_batch`.
  `_protect_real_file()` backup/restore (qua `atexit`) `TRACKER_PATH` và
  `GEMINI_TRACKER_PATH` thật trước khi chạy — `translate()`/
  `translate_batch()` ghi thẳng vào 2 file đó, không có bản test riêng.
- `test_paragraphs.py` — `make_block()` dựng block giả, test
  `merge_paragraph_blocks`/`split_incoherent_block`.
- `test_pdf_convert.py` — `make_sample_pdf()`, test `convert_to_docx`/
  `convert_to_pptx` end-to-end; `make_pdf_with_image()` test ảnh nhỏ được
  crop thành picture shape riêng đúng vị trí/kích thước; `make_pdf_with_
  large_image()` test ảnh LỚN (>60% trang, trang không scan) vẫn được crop
  riêng, không bị coi nhầm là ảnh nền toàn trang; `make_scanned_pdf_
  with_red_text()` (cần `ocr_cli` đã build) test màu chữ OCR khớp màu mực
  thật, không phải đen mặc định; `table_lines`/`dense_table_lines` (dựng
  thẳng `lines` giả lập, không cần OCR) test nhãn hàng wrap 2 dòng được
  gộp đúng 1 hàng + ô cao không rơi dòng sang hàng sau
  (`_detect_table`/`_build_table_cells`), và bảng quá dày đặc (ô >
  `TABLE_MAX_CELL_LINES`) bị từ chối dựng thành bảng; test nền vector thật
  (hình chữ nhật tô màu, không phải ảnh/chữ) chỉ bị xóa ĐÚNG PHẦN
  `table_bbox` truyền vào `_page_background_png()`, không lây sang phần
  còn lại của trang; test kích thước THẬT của bảng do `_add_table_shape()`
  dựng ra (tổng độ rộng cột/tổng chiều cao hàng cộng dồn) khớp CHÍNH XÁC
  với `_table_bbox_rect()` — bug thật đã gặp: lệch 4pt do `col_edges`/
  `row_edges` tính sai điểm bắt đầu; test `_table_crosses_section_divider()`
  phát hiện đúng 1 thanh chia nhóm THẬT (gần hết chiều rộng trang, đủ cao)
  cắt qua vùng bảng, không báo động giả với vạch kẻ mỏng bình thường.
- `test_office_translate.py` — stub `router._deepl_batch` (không gọi
  mạng), test `translate_docx` (đoạn văn 2 run giữ đúng định dạng run đầu
  khi bề rộng bản dịch không phình, bảng, header) và `translate_pptx`
  (text box, bảng, group shape đệ quy, `max_pages` chỉ dịch N slide đầu —
  slide sau phải giữ nguyên bản gốc); `make_long_stub_router()` (bản dịch
  dài gấp 3) test cụm ngắn KHÔNG bị bỏ dịch (enforce_length_guard=False)
  và cỡ chữ tự giảm (`_adjust_font_for_length`, đo bằng `_text_width_ratio`
  thật chứ không đếm ký tự) + `auto_size` bật đúng cho text box;
  `make_double_stub_router()` test ô bảng co NHIỀU HƠN text box với cùng
  mức phình (ngưỡng riêng `FONT_SHRINK_MIN_SCALE_TABLE` thấp hơn) và
  `word_wrap` bị ép bật cho ô bảng dù file gốc tắt; `make_extreme_stub_
  router()` (bản dịch phình cực nhiều trong bảng hẹp) test co chữ chạm
  đúng ngưỡng tối thiểu CỘNG chiều cao hàng phải GIÃN thêm
  (`_grow_table_rows_to_fit`) khi co chữ vẫn không đủ chỗ — so khớp với số
  dòng tính độc lập qua `_wrapped_line_count()`.
- `test_ocr_pdf.py` — test `ensure_text_layer`/`_page_has_real_text` trên
  PDF thật + PDF scan giả lập (cần `ocr_cli` đã build); test
  `_detect_vertical_grid_lines()` dò đúng 1 đường kẻ vẽ sẵn ở vị trí biết
  trước, không báo động giả trên trang trắng.
