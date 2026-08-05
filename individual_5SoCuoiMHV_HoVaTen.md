# Báo cáo cá nhân — Day 9: Multi-Agent E-commerce Dispute Resolution

> Điền thông tin trong dấu `[]` bằng phần việc bạn trực tiếp thực hiện trước khi nộp.

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | [Họ và tên] |
| MSSV | [MSSV] |
| Khóa/Lớp | K4 / [Lớp] |
| Vai trò chính | [Ví dụ: Verifier Agent và tích hợp pipeline] |
| Ngày hoàn thành | [YYYY-MM-DD] |

## 2. Phần việc sở hữu

| Module/deliverable | File phụ trách | Input | Output | Trạng thái |
| --- | --- | --- | --- | --- |
| [Phần việc trực tiếp thực hiện] | [Đường dẫn file/hàm] | [Input] | [Output] | [Hoàn thành/Một phần] |
| [Phần việc trực tiếp thực hiện] | [Đường dẫn file/hàm] | [Input] | [Output] | [Hoàn thành/Một phần] |

Phần việc hỗ trợ ngoài ownership chính: [Mô tả ngắn, nếu có].

## 3. Kết quả và cách xác minh

Nêu một artifact có thể kiểm chứng do phần việc của bạn tạo ra: [Ví dụ: output JSON, trace handoff, validator report].

```powershell
.\.venv\Scripts\Activate.ps1
python run.py
(Get-ChildItem output -Filter 'EC_*.json').Count
```

- Kết quả mong đợi: pipeline chỉ ghi đủ 50 output khi 50 input đều qua verifier.
- Kết quả thực tế: [Điền sau khi chạy với input chính thức.]
- Artifact/log: `[Đường dẫn]`.

## 4. Giải thích kỹ thuật

### Vấn đề giải quyết

[Mô tả domain bạn đảm nhận: customer history, order/product, payment, delivery, policy hoặc validation.]

### Cách triển khai

[Giải thích cách bạn join dữ liệu, tính toán, truyền handoff và xử lý null. Nêu rõ bạn chỉ dùng bằng chứng có thể dựng trực tiếp từ CSV.]

### Input/output contract

| Thành phần | Mô tả |
| --- | --- |
| Input | [Schema/handoff hoặc CSV rows] |
| Output | [Handoff/result fields] |
| Module phụ thuộc | [Tên agent/file] |
| Module dùng output | [Tên agent/file] |
| Điều kiện lỗi | [Ví dụ: missing order, vượt limit, evidence sai format] |

## 5. Một quyết định kỹ thuật

- Bối cảnh: [Vấn đề cần chọn giải pháp.]
- Phương án cân nhắc: [Ít nhất hai phương án.]
- Phương án đã chọn: [Phương án.]
- Lý do: [Đánh đổi về correctness, auditability, cost hoặc reproducibility.]
- Bằng chứng: [Lệnh test, trace hay output xác minh.]

## 6. Lỗi hoặc blocker đã xử lý

- Triệu chứng/bước tái hiện: [Lệnh hoặc tình huống.]
- Nguyên nhân gốc: [Root cause.]
- Cách xử lý: [Thay đổi cụ thể.]
- Xác minh sau sửa: [Lệnh và kết quả.]
- Bài học: [Điều rút ra.]

## 7. Hiểu biết end-to-end

1. Case dùng `claimed_order_id` để tra order, sau đó join customer, item, payment và product bằng các khóa Olist. Customer history dùng `customer_unique_id`, không đưa lịch sử vào `affected_entities`.
2. Các agent trả handoff có cấu trúc. Coordinator chuyển item handoff cho Payment/Delivery, rồi Policy áp dụng `EC_POLICY_V2` theo thứ tự ưu tiên.
3. Verifier kiểm tra case/order, giới hạn mảng, confidence, item/payment IDs và evidence prefix trước khi ghi staging output.
4. `trace.jsonl` là trace của đúng lượt chạy mới nhất; `metadata.json` khai báo runtime/model. Chỉ zip 50 JSON trong `output/`, không đưa source hoặc secret vào zip.

## 8. Cam kết

- [ ] Báo cáo phản ánh đúng phần việc trực tiếp của tôi.
- [ ] Tôi có thể giải thích luồng end-to-end và policy.
- [ ] Tôi chỉ ghi nhận kết quả đã được chạy và xác minh.
- [ ] Báo cáo không chứa API key, token hay secret.

**Họ và tên:** [Họ và tên]
