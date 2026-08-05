# Báo cáo cá nhân — Day 9: Multi-Agent E-commerce Dispute Resolution

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Lăng Nhật Minh |
| 5 số cuối MHV | 01482 |
| Bài tập | K4 Day 09 — Multi-Agent E-commerce Dispute Resolution |
| Vai trò | Thiết kế và triển khai pipeline multi-agent, kiểm chứng output và tích hợp local LLM |
| Ngày hoàn thành | 05/08/2026 |

## 2. Mục tiêu bài toán

Xây dựng hệ thống điều tra 50 khiếu nại thương mại điện tử dựa trên dữ liệu Olist. Với mỗi `claimed_order_id`, hệ thống phải liên kết dữ liệu customer, order, item, product và payment; phân tích giao vận; sau đó kết luận theo `EC_POLICY_V2` và ghi một JSON đúng schema.

Mục tiêu kỹ thuật của em là đảm bảo kết quả có thể chạy lại, có trace handoff giữa các agent, không tự tạo evidence ngoài dữ liệu CSV và không ghi output chưa qua kiểm chứng.

## 3. Phần việc thực hiện

| Hạng mục | Thành phần chính | Kết quả |
| --- | --- | --- |
| Thiết kế luồng multi-agent | `architecture.md`, `CoordinatorAgent` | Chia luồng điều tra theo domain và tổng hợp qua handoff có cấu trúc. |
| Truy xuất customer và product | `CustomerAgent`, `OrderProductAgent` | Xác định customer unique ID, lịch sử order, item, seller, product và category. |
| Đối soát payment và giao vận | `PaymentAgent`, `DeliveryAgent` | Tính tổng tiền, sai lệch, trạng thái đối soát, delivery variance và seller handoff variance. |
| Ra quyết định policy | `PolicyAgent`, `PolicyDeliberationAgent` | Áp dụng thứ tự ưu tiên `EC_POLICY_V2`; có chế độ thảo luận local LLM theo vai trò proposer–critic–finalizer. |
| Kiểm chứng và xuất kết quả | `VerifierAgent`, `run_pipeline` | Chặn ID/evidence không hợp lệ, kiểm tra null handling và chỉ thay output sau khi toàn bộ 50 case đạt. |

## 4. Kiến trúc và luồng xử lý

```text
input/EC_*.json
      ↓
CoordinatorAgent
 ├─ CustomerAgent       → customer_context
 ├─ OrderProductAgent   → item, seller, product, category
 ├─ PaymentAgent        → payment reconciliation
 ├─ DeliveryAgent       → delivery và seller handoff analysis
 ├─ PolicyAgent / Local LLM policy deliberation
 └─ VerifierAgent       → output/EC_*.json
      ↓
logging/trace.jsonl và logging/metadata.json
```

Mỗi agent chỉ nhận dữ liệu cần thiết cho domain của mình. `PaymentAgent` và `DeliveryAgent` dùng item handoff từ `OrderProductAgent`; `PolicyAgent` chỉ dùng các handoff đã chuẩn hóa thay vì đọc CSV trực tiếp. `CoordinatorAgent` dựng evidence ID từ ID đã được xác nhận trong source data rồi chuyển sang `VerifierAgent`.

## 5. Các quyết định kỹ thuật

### Dùng tính toán quyết định cho dữ liệu và kiểm chứng

Các trường có thể xác minh trực tiếp từ CSV như số tiền, timestamp, ID, delivery variance và payment reconciliation được tính bằng Python standard library. Tiền BRL được xử lý bằng `Decimal`, làm tròn hai chữ số thập phân; chênh lệch payment dùng ngưỡng `0.10 BRL` theo đề bài.

Lý do chọn cách này là các trường trên cần chính xác, có thể audit và không nên phụ thuộc vào suy đoán của mô hình ngôn ngữ.

### Local LLM theo nhiều vai trò, không dùng để bịa dữ liệu

Chế độ `--policy-mode llm` sử dụng Qwen3 4B chạy local qua LM Studio. Cùng một model lần lượt đảm nhiệm ba role:

1. Policy Proposer chọn primary issue từ evidence packet.
2. Policy Critic kiểm tra proposal với facts và điều kiện policy công khai.
3. Policy Finalizer đưa ra lựa chọn cuối cùng theo JSON contract.

LLM chỉ nhận evidence packet chỉ đọc; không sửa `input/` hoặc `data/`. Các refund, action, evidence ID và schema cuối cùng vẫn được tạo từ policy công khai và được verifier kiểm tra. Cách này giúp thể hiện handoff/đánh giá chéo giữa agent, đồng thời giảm rủi ro model trả JSON sai hoặc suy luận ra dữ liệu không tồn tại.

## 6. Kiểm chứng và kết quả

Lệnh chạy pipeline local LLM:

```powershell
.\.venv\Scripts\Activate.ps1
python run.py --policy-mode llm --llm-parameter-size 4B --category-language source --output-dir output_llm_agents_source_candidate --trace logging/trace_llm_agents_source_candidate.jsonl --metadata logging/metadata_llm_agents_source_candidate.json
```

Các kiểm tra sau khi chạy:

```powershell
(Get-ChildItem output -Filter 'EC_*.json').Count
(Get-Content logging/trace_llm_agents_source_candidate.jsonl | Measure-Object -Line).Lines
Get-Content logging/metadata_llm_agents_source_candidate.json
```

- Pipeline sinh đủ 50 file JSON từ `EC_001.json` đến `EC_050.json`.
- Trace ghi nhận handoff Customer, Order/Product, Payment, Delivery, Policy Proposer, Policy Critic, Policy Finalizer và Verifier.
- Metadata ghi rõ model local `qwen/qwen3-4b`, parameter size `4B`, runtime local và policy version `EC_POLICY_V2`.
- Kết quả leaderboard đã đạt khoảng 67 điểm; các output được audit lại với input và CSV để kiểm tra ID, số tiền, evidence, null handling và policy conditions.

## 7. Lỗi đã xử lý và bài học

Trong quá trình tích hợp LM Studio, model local đôi khi trả thêm diễn giải hoặc không thỏa JSON contract. Em xử lý bằng JSON Schema cho output policy, `temperature=0`, giới hạn token, retry cục bộ và kiểm tra chặt key/value trước khi chấp nhận kết quả.

Một lỗi khác là model chọn primary issue không khả thi theo facts. Để xử lý, Critic và Finalizer nhận thêm kết quả từ Policy Eligibility Tool chỉ đọc; kết quả cuối chỉ được dùng khi phù hợp với điều kiện policy, nếu không case bị từ chối thay vì sinh output sai.

Bài học rút ra là LLM phù hợp cho bước đánh giá/quyết định có contract rõ ràng, còn dữ liệu định lượng và evidence phải được tính, truy vết và xác minh từ nguồn dữ liệu gốc.

## 8. Cam kết

- Báo cáo phản ánh phần triển khai cá nhân của em trong bài tập.
- Em có thể giải thích luồng end-to-end, điều kiện `EC_POLICY_V2`, cách các agent handoff và cách verifier hoạt động.
- Không đưa API key, token hoặc secret vào source code, trace, metadata hay file nộp.
- Khi nộp output, chỉ nén 50 file JSON trong thư mục `output/`.

**Họ và tên:** Lăng Nhật Minh  
**5 số cuối MHV:** 01482
