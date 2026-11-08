# Chạy tự động và xem trên laptop qua USB

## Luồng hoạt động

- Bật robot: Feather dog tự dùng chu kỳ mặc định 2 giây, không cần bấm nút robot.
- Khi radio, khóa ghép cặp và đồng hồ sẵn sàng, dog tự gửi yêu cầu tới vòng đã đăng ký.
- Người đeo làm thao tác **bấm–thả 2 lần, lần thứ ba giữ >=2 giây rồi thả**.
- Dog xác minh, trả ACK và chờ 2 giờ trước khi hỏi lại vòng đó. Vòng sáng thêm
  5 giây sau ACK rồi tắt theo mức điều khiển LED. Vòng khác vẫn được hỏi.
- Laptop hiển thị log/trạng thái qua USB dog; không xác nhận thay người đeo,
  không ra lệnh di chuyển, không tự xóa thời gian chờ.
- Không giới hạn cứng 100 m; hai radio phải ở trong vùng liên lạc thực tế.

## Mở màn hình trên Windows

Cài đặt/nạp firmware và ghép cặp vẫn cần thực hiện một lần như hướng dẫn nạp.
Sau khi đồng bộ giờ cho vòng, giữ vòng có nguồn. Kết nối Feather dog với laptop
(ví dụ COM9), đóng Serial Monitor và chạy từ thư mục repository:

```powershell
.\.venv\Scripts\python.exe scripts\checkin_monitor.py --port COM9
```

Trình duyệt tự mở màn hình; nếu không, dùng địa chỉ chương trình in ra (mặc định
`http://127.0.0.1:8012/`). Giữ chương trình chạy cho buổi sử dụng. Sau đó bật/tắt
robot không cần gõ lệnh bắt đầu từng lượt: chương trình tự nối lại USB và đặt giờ
cho dog nếu dog báo chưa có giờ. Nó không gửi lệnh xác nhận, xóa chờ hay di chuyển.

Màn hình hiển thị ID vòng, chờ thao tác nút/đã xác nhận, thời gian chờ hỏi lại
và log USB. Nếu mở màn hình sau khi một vòng đã xác nhận thì chưa biết chính xác
thời điểm bắt đầu đếm; màn hình không tự bịa thời gian còn lại. Mất USB làm dữ
liệu được đánh dấu cũ. `CONFIRMED_WAIT_2H` không có nghĩa vòng đang ở gần hoặc
người đeo đang an toàn. ACK trên đèn vòng là kiểm tra riêng cho đường về.

Nhấn Ctrl+C trong terminal để đóng chương trình và nhả COM9 trước khi nạp code,
đồng bộ giờ hoặc xóa chờ. Nếu đổi cổng USB làm COM thay đổi, dùng cổng mới trong
`--port`. `--no-sync-clock` cho phép chỉ đọc trạng thái mà không tự đặt giờ dog.

## Giới hạn khi bật nguồn

**Chưa có RTC và chưa có đồng bộ giờ qua radio khi khởi động.**

- Dog vừa mất nguồn cần được đặt giờ: chương trình laptop đang chạy làm việc đó
  tự động qua USB; sau đó dog hoạt động độc lập trong khi còn nguồn.
- Vòng phải được đồng bộ giờ một lần và giữ nguồn để đồng hồ tiếp tục chạy.
  Nếu XIAO/vòng mất nguồn, cần đồng bộ lại vòng qua USB bằng công cụ hiện có.
- Vì vậy bản này chưa đạt "cả robot và vòng vừa mất nguồn, bật lại là tự chạy
  hoàn toàn không có máy tính". Muốn vậy cần RTC/nguồn giữ giờ hoặc thiết kế
  cơ chế khởi động đồng bộ qua radio có xác thực. Không đoán giờ hay bỏ kiểm tra
  xác thực để làm cho trạng thái sẵn sàng giả.

## Xóa chờ 2 giờ để thử ngay

Cần nạp bản mới cho **dog và XIAO**, giữ nguyên các file khóa. Feather modem trên
vòng không cần nạp lại cho tính năng xóa chờ/màn hình USB.

Đóng màn hình/Serial Monitor. Cắm dog COM9 và XIAO COM8, chạy:

```powershell
.\.venv\Scripts\python.exe scripts\set_clocks.py --port COM9 --port COM8 --auto 2 --clear-wait
```

Công cụ đặt giờ, kiểm tra cả hai firmware hỗ trợ, rồi gửi lệnh USB **`#F`** cho
vòng trước, dog sau. Phải thấy `#WAIT_CLEARED band` và `#WAIT_CLEARED dog`.
Nó xóa thời gian chờ và lượt đang dở, **không xóa ID/khóa hay bộ nhớ chống phát lại**.
Sau đó mở lại chương trình màn hình để theo dõi lượt mới.

Có thể gửi `#F` riêng trong monitor USB từng bo, nhưng phải làm cả dog và XIAO.
Vòng không nhận `#F` qua BLE. Lệnh `#A 2` và các lần đồng bộ giờ thông thường vẫn
không xóa bộ chờ. Nếu firmware cũ hoặc một cổng bị thiếu, công cụ báo lỗi thay vì
âm thầm coi đã xóa thành công. Đây là công cụ thử nghiệm, không bắt buộc dùng
trong các lượt xác nhận bình thường.
