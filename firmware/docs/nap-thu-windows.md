# Nạp thử trên Windows: dog và wristband RFM69HCW

## 1. Chuẩn bị

Phần cứng của bản này:

- Dog: **Adafruit Feather 32u4 RFM69HCW hoặc RFM9x LoRa 900 MHz**.
- Vòng: **XIAO ESP32-S3 + Feather 32u4 RFM69HCW**, có nút bấm và đèn/rung.
- Board và anten đúng băng tần **900/915 MHz**. Code hiện dùng 903–927 MHz;
  nếu board của bạn là 433 MHz hoặc Feather M0/M4, không chọn cấu hình này.
- Cáp USB có truyền dữ liệu. Gắn anten trước khi bật radio.

Mã đã qua kiểm thử trên máy tính nhưng **chưa xác nhận build/nạp trên board thật**.
Hãy chạy bước build trước. Nếu báo thiếu bộ nhớ hoặc lỗi biên dịch, giữ nguyên log
để xử lý; chưa coi firmware là đã sẵn sàng cho phần cứng.

Cài **Python 3.12** (chọn Add Python to PATH) và Git. Mở **Command Prompt (cmd)**,
không dùng PowerShell cho lệnh kích hoạt bên dưới:

```bat
git clone https://github.com/vandeptrai11012008-star/test.git
cd test
python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install platformio==6.1.18 pyserial==3.5 uv
```

Nếu đã có clone, lưu thay đổi của bạn rồi chạy `git pull --ff-only origin main`
thay vì clone lại. Các lệnh tiếp theo đều chạy từ thư mục `test`.

Xem cổng USB trong **Device Manager > Ports (COM & LPT)** hoặc:

```bat
python -m platformio device list
```

Các số COM dưới đây chỉ là ví dụ, phải thay bằng cổng của bạn. Khi nạp, cắm từng
board để tránh nhầm. Đóng Serial Monitor trước mỗi lần nạp hoặc chạy script đồng hồ.

## 2. Nạp XIAO lần đầu để đọc ID

Nếu đã có `firmware\include\secrets.h`, sao lưu riêng trước khi thay thế ở bước 4.
Một checkout mới chưa có file này vẫn build được để đọc ID; chưa phát beacon ghép cặp.

Cắm XIAO và xác định cổng (ví dụ COM6):

```bat
python -m platformio run -d firmware -e band_xiao_feather
python -m platformio run -d firmware -e band_xiao_feather -t upload --upload-port COM6
python -m platformio device monitor --port COM6 --baud 115200
```

Gõ `#I` rồi Enter. Ghi lại ID trong dòng:

```text
#PAIR_ID 0000AABBCCDDEEFF paired=0
```

Đây chỉ là ví dụ. Dùng **ID thật của XIAO của bạn**, đủ 16 ký tự hex, không lấy
các chữ số trong tên Bluetooth. Nhấn Ctrl+C để đóng monitor. Nếu không nạp được,
đưa XIAO vào bootloader bằng nút BOOT/RESET theo hướng dẫn board; kiểm tra lại COM
vì cổng bootloader có thể khác. Cấu hình XIAO chạy esptool qua module Python 3.12 bằng `uv`, tránh lỗi chạy
trực tiếp file `.py` trên Windows. Nếu báo thiếu `uv`, thêm `.venv\Scripts`
vào PATH của terminal PowerShell:

```powershell
$env:Path = "$(Resolve-Path .\.venv\Scripts);$env:Path"
```

Nếu báo `Failed to spawn: esptool.py`, cập nhật code bằng `git pull --ff-only origin main`
để lấy lệnh nạp mới. Lệnh nạp sử dụng cổng được truyền qua `--upload-port`.

## 3. Tạo khóa ghép cặp

Thay ID ví dụ bằng ID vừa đọc. `D001` là ID bạn chọn cho dog đầu tiên:

```bat
python scripts\provision_periodic.py --dog-id D001 --band-id 0000AABBCCDDEEFF --out-dir ..\rescue-private-pairing
```

Muốn thêm vòng khác, thêm `--band-id ID_CUA_VONG_KHAC` vào **cùng lần tạo**;
bản này tối đa 4 vòng. Mỗi vòng phải lấy ID từ XIAO của chính nó.

Thư mục riêng chứa khóa. Không tải các file này lên GitHub, không gửi chúng trong
log hoặc chat. Script từ chối ghi đè thư mục cũ; tạo bộ khóa mới sẽ cần nạp lại các
board liên quan. Giữ bản sao an toàn nếu cần nạp lại sau này.

## 4. Nạp lần lượt ba board

### Feather trên dog (ví dụ COM5)

**Chọn đúng chip theo chữ trên board:**

- `RFM69HCW`: dùng target `dog_feather_rfm69` trong lệnh bên dưới.
- `RFM9x LoRa`: thay target bằng **`dog_feather_periodic_rfm9x`**. Target này
  chạy FSK để nói chuyện với vòng RFM69, không dùng LoRa modulation.
- Nếu nạp nhầm target RFM69 vào board RFM9x, radio có thể báo `code=-2`.


```bat
copy ..\rescue-private-pairing\dog-secrets.h firmware\include\secrets.h
python -m platformio run -d firmware -e dog_feather_rfm69
python -m platformio run -d firmware -e dog_feather_rfm69 -t upload --upload-port COM5
```

Không chọn `dog_feather`: đó là luồng near-check cũ, không phải check-in định kỳ.

### Feather radio trên vòng (ví dụ COM7)

```bat
copy ..\rescue-private-pairing\radio-secrets.h firmware\include\secrets.h
python -m platformio run -d firmware -e feather_radio
python -m platformio run -d firmware -e feather_radio -t upload --upload-port COM7
```

### XIAO trên vòng (ví dụ COM6)

Tên file phải khớp ID thật của XIAO:

```bat
copy ..\rescue-private-pairing\band-0000AABBCCDDEEFF-secrets.h firmware\include\secrets.h
python -m platformio run -d firmware -e band_xiao_feather
python -m platformio run -d firmware -e band_xiao_feather -t upload --upload-port COM6
```

Nếu Windows hỏi ghi đè `secrets.h`, chỉ xác nhận sau khi đã sao lưu file cũ.
Mỗi lần đổi header phải build/upload đúng board tương ứng. Chỉ nạp firmware,
không đụng đến chương trình điều khiển chuyển động của MechDog.

## 5. Đồng bộ giờ và thử

Nối Feather modem với XIAO theo sơ đồ có sẵn: RX/TX chéo, chung GND, mức logic
3,3 V, baud 38400. Mỗi board phải có nguồn phù hợp. Xem sơ đồ
[wristband-wiring.pdf](wristband-wiring.pdf); không tự đổi dây theo số COM.

Cắm dog Feather và XIAO vào USB, giữ cả modem vòng có nguồn. Đóng tất cả monitor:

```bat
python scripts\set_clocks.py --port COM5 --port COM6 --auto 2
```

Sau đó xem log dog:

```bat
python -m platformio device monitor --port COM5 --baud 115200
```

Gõ `#?` rồi Enter. Cần thấy `radio=ok`, `clock=set`, `paired=1` (hoặc số vòng đã
đăng ký), `auto=2`. Nếu mở monitor làm board reset và hiện `clock=NOT-set`, đóng
monitor và chạy lại script đồng hồ. Sau khi đồng bộ vẫn có thể thử bằng nút/đèn,
không bắt buộc mở monitor.

Kết quả mong đợi:

1. Dog phát `#BEACON band=... wait=button` mỗi 2 giây với một vòng đang chờ.
2. Vòng rung/nháy đèn một lần cho cửa sổ hỏi 30 giây. Dog có thể in
   `#BAND_SEEN band=... wait=button` khi nghe được trả lời tự động.
3. Trong 30 giây, **bấm–thả hai lần**, sau đó **bấm lần thứ ba, giữ ít nhất 2 giây
   rồi thả**. Hai lần bấm nhanh mỗi lần không quá 0,5 giây; khoảng nghỉ không quá
   1,5 giây. Không bấm nút RESET. Dog in `#PERSON_CONFIRMED band=... next=2h`.
   Khi vòng nhận gói báo nhận đúng từ dog, nó in `#CONFIRMED led=5s`: đèn trạng
   thái sáng 5 giây rồi tắt. Đèn cũng sáng trong lúc đang bấm nút; đèn nguồn/sạc
   không thuộc chức năng này.
4. Gõ `#?`: vòng đó ở `CONFIRMED_WAIT_2H`. Dog không hỏi lại nó trong 2 giờ;
   vòng khác chưa xác nhận vẫn được hỏi. Không cần chờ 2 giờ để thử các bước 1–3.

`#A 0` dừng phát; `#A 2` bật lại chu kỳ 2 giây, **không xóa** bộ chờ 2 giờ.
Muốn bắt đầu lại phép thử ngay, khởi động lại **cả dog và XIAO**, rồi đồng bộ giờ
lại. Bộ chờ hiện lưu RAM; mất nguồn sẽ mất trạng thái này.

Thử hai radio ở gần trước, sau đó tăng khoảng cách từ từ. Không giới hạn cứng
100 m; khoảng phủ sóng thực tế phụ thuộc anten và vật cản.

## Nếu chưa hoạt động

- `paired=0`: đã nạp đúng file khóa của từng board chưa?
- `clock=NOT-set`: chạy lại đồng bộ giờ sau lần reset cuối.
- `radio=missing`: kiểm tra đúng model board, nguồn và kết nối.
- Không thấy vòng phản hồi: kiểm tra ID, khóa từng cặp, hopping key trên cả ba
  board, dây XIAO–Feather và nguồn cho modem.
- Cổng COM bận: đóng monitor hoặc ứng dụng đang mở cổng đó.
- Lỗi upload 32u4: reset board để vào bootloader, kiểm tra lại cổng COM rồi nạp lại.

Log chỉ cần có dòng lỗi và ID; không gửi nội dung `secrets.h`. Chi tiết giao thức,
phạm vi và các giới hạn nằm trong [periodic-rfm69.md](periodic-rfm69.md).

### Đọc trực tiếp chip khi dog báo `code=-2`

Target `feather_radio_probe` chỉ dùng USB/SPI, không dùng RadioLib, không nạp khóa
và không phát sóng. Đóng monitor rồi nạp tạm vào **Feather dog**:

```bat
python -m platformio run -d firmware -e feather_radio_probe -t upload --upload-port COM9
python -m platformio device monitor --port COM9 --baud 115200 --echo --filter send_on_enter --eol LF
```

Gõ `#?` rồi Enter. Gửi dòng `#SPI ...` để chẩn đoán. Với RFM69 giao tiếp được,
`RF69_VERSION` dự kiến đọc ổn định `24 24 24` (hex). Kết quả khác chưa tự nó chứng
minh chip hỏng. Sau phép thử cần nạp lại `dog_feather_rfm69` và đồng bộ giờ để
khôi phục hoạt động; bản probe thay thế firmware check-in trên board đó.

### Bản thao tác nút và đèn mới

Module nút có đèn: **SIG1 nối XIAO D1** (điều khiển LED), **SIG2 nối D3**
(đọc nút), chung GND. Cấu hình `PIN_BUTTON_LED = D1` điều khiển đèn trên module:
sáng khi bấm, sáng 5 giây sau ACK hợp lệ rồi tắt. Không nối SIG1 trực tiếp vào
nguồn nếu muốn firmware điều khiển đèn. Bản thay đổi chân LED chỉ cần nạp lại XIAO,
không tạo lại khóa hoặc nạp lại các Feather nếu đã có bản thao tác nút và ACK.

Cần nạp lại **dog, XIAO và Feather radio trên vòng** với các file khóa cũ tương
ứng, rồi đồng bộ giờ. Không tạo lại khóa. Với board dog ghi `RFM9x LoRa`, dùng
`dog_feather_periodic_rfm9x`; XIAO dùng `band_xiao_feather`; modem vòng RFM69 dùng
`feather_radio`. Vòng mới chờ ACK từ dog nên không hiện đèn thành công nếu dog
vẫn chạy bản cũ. `#P` qua USB/BLE không thay được thao tác vật lý cho yêu cầu định kỳ.

### Đèn module vẫn sáng sau khi thả nút

Trên monitor **XIAO**, gửi `#?` để đọc `led_pin`, `button_raw` và `pressed_level`.
Trên XIAO ESP32-S3, D1 tương ứng GPIO 2. Gửi `#E` (E viết hoa), không bấm nút:
firmware giữ chân đèn LOW 5 giây, rồi HIGH 5 giây, và in trạng thái tương ứng.
Phép thử tạm dừng xử lý vòng trong 10 giây, không phát gói radio. Ghi lại đèn ở
cả hai mức. Chưa tự đảo mức LED nếu đèn không đổi theo hai mức này; cần kiểm tra
chân SIG1, nguồn/GND và module. Sau `#LED_TEST done`, logic thường tiếp tục nên
đèn có thể đổi lại theo trạng thái nút.
