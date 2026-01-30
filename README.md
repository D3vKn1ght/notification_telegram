# Telegram Reminder Bot

Một bot Telegram mạnh mẽ để quản lý và gửi tin nhắn nhắc nhở (reminder) tự động trong các nhóm hoặc chat riêng tư. Bot hỗ trợ nhắc nhở theo ngày giờ cụ thể, lặp lại theo chu kỳ, hoặc nhắc nhở theo thứ trong tuần.

## Tính năng

- **Nhắc nhở định kỳ**: Đặt lịch gửi tin nhắn vào thời gian cụ thể và lặp lại sau mỗi X ngày.
- **Nhắc nhở theo tuần**: Đặt lịch gửi tin nhắn vào các thứ trong tuần (ví dụ: Thứ 2, Thứ 4 hàng tuần).
- **Quản lý linh hoạt**: Xem danh sách, xóa, tạm dừng (pause), tiếp tục (resume) hoặc hoãn (snooze) nhắc nhở.
- **Cấu hình nhóm**: Tùy chỉnh múi giờ (Timezone) cho từng nhóm, bật/tắt toàn bộ bot trong nhóm.
- **Backup dữ liệu**: Xuất dữ liệu nhắc nhở ra file JSON để sao lưu.
- **Phân quyền**: Chỉ Admin của nhóm mới có thể thực hiện các lệnh thay đổi cài đặt.

## Cài đặt

### Yêu cầu hệ thống
- Python 3.7 trở lên
- Tài khoản Telegram và Token bot (lấy từ @BotFather)

### Các bước cài đặt

1. **Cài đặt các thư viện cần thiết:**
   ```bash
   pip install python-telegram-bot python-telegram-bot[job-queue] pytz
   ```

2. **Cấu hình Token:**
   Mở file `config_telegram.py` và điền token bot của bạn vào biến `TOKEN`:
   ```python
   TOKEN="YOUR_TELEGRAM_BOT_TOKEN_HERE"
   ```

3. **Chạy bot:**
   ```bash
   python telegrambot.py
   ```

## Hướng dẫn sử dụng

Đầu tiên, hãy thêm bot vào nhóm và gõ lệnh `/start` để kích hoạt.

### Các lệnh chính (Dành cho Admin)

Các lệnh cấu hình thường yêu cầu tham số dạng JSON.

| Lệnh | Mô tả | Ví dụ Payload (JSON) |
|------|-------|----------------------|
| `/set_message` | Thêm hoặc sửa nhắc nhở theo ngày/giờ cụ thể. `duration` là chu kỳ lặp lại (ngày). | `{"time_receive":"2026-01-30 20:00", "duration":1, "message":"Nhắc nhở họp team"}` |
| `/set_message_week` | Thêm nhắc nhở lặp lại theo thứ trong tuần. | `{"list_week":["T2","T3","CN"], "time":"09:00", "message":"Báo cáo tiến độ"}` |
| `/delete_message` | Xóa vĩnh viễn một nhắc nhở theo ID. | `{"id": 1}` |
| `/pause` | Tạm dừng một nhắc nhở cụ thể. | `{"id": 1}` |
| `/resume` | Bật lại một nhắc nhở đã dừng. | `{"id": 1}` |
| `/snooze` | Hoãn nhắc nhở thêm X phút (tính từ lúc gõ lệnh). | `{"id": 1, "minutes": 15}` |
| `/set_timezone` | Đổi múi giờ cho nhóm (Mặc định: Asia/Ho_Chi_Minh). | `{"tz": "Asia/Bangkok"}` |
| `/pause_all` | Tạm dừng tất cả nhắc nhở trong nhóm. | (Không cần tham số) |
| `/resume_all` | Bật lại tất cả nhắc nhở trong nhóm. | (Không cần tham số) |
| `/export` | Tải về file backup dữ liệu hiện tại. | (Không cần tham số) |

### Các lệnh chung (Mọi người)

| Lệnh | Mô tả |
|------|-------|
| `/get_message` | Xem danh sách các nhắc nhở hiện có, bao gồm ID, thời gian nhận và trạng thái. |

## Định dạng dữ liệu

- **Thời gian (`time_receive`)**: `YYYY-MM-DD HH:MM` (Ví dụ: `2026-01-30 14:30`)
- **Giờ (`time`)**: `HH:MM` (Ví dụ: `09:00`)
- **Thứ trong tuần**:
  - `T2`: Thứ Hai
  - `T3`: Thứ Ba
  - `T4`: Thứ Tư
  - `T5`: Thứ Năm
  - `T6`: Thứ Sáu
  - `T7`: Thứ Bảy
  - `CN`: Chủ Nhật

Dữ liệu của bot sẽ được lưu tự động vào file `data.json` cùng thư mục.