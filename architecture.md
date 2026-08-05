# Architecture — EC_POLICY_V2 Dispute Resolution

## Mục tiêu

Hệ thống điều tra 50 yêu cầu hỗ trợ Olist theo `EC_POLICY_V2`. Mỗi agent chỉ xử lý domain dữ liệu của mình; `CoordinatorAgent` không tự suy diễn dữ liệu ngoài các handoff. Tất cả quyết định là deterministic để có thể chạy lại và audit.

```text
input/EC_*.json
      |
      v
CoordinatorAgent ──> CustomerAgent ──────── customer_context
      |                 (customers, orders)
      +──────────────> OrderProductAgent ─── items, sellers, products, categories
      |                 (orders, order_items, products)
      +──────────────> PaymentAgent ───────── reconciliation
      |                 (order_payments + item handoff)
      +──────────────> DeliveryAgent ──────── delivery and seller handoff analysis
      |                 (orders + item handoff)
      +──────────────> PolicyAgent ────────── EC_POLICY_V2 decision
      |                 (all investigation handoffs)
      +──────────────> VerifierAgent ──────── schema/ID/limit guard
      |                 (assembled result + source IDs)
      v
output/EC_*.json, logging/trace.jsonl, logging/metadata.json
```

## Agent contracts and least-privilege access

| Agent | Read access | Handoff returned | Không được làm |
| --- | --- | --- | --- |
| Coordinator | Case request and agent handoffs | Final candidate result | Không tự tạo event/evidence ngoài dữ liệu |
| Customer | `orders`, `customers` | `customer_unique_id`, capped `related_order_ids` | Không đưa order lịch sử vào `affected_entities` |
| Order & Product | `order_items`, `products` | Item, seller, product, category IDs | Không tính payment/refund |
| Payment | `order_payments` và item handoff | Totals, difference, reconciliation, payment IDs | Không kết luận trách nhiệm |
| Delivery | Order timestamps và item handoff | Delivery/handoff variances, late sellers | Không suy diễn tracking checkpoint |
| Policy | Handoff đã chuẩn hoá | Primary/secondary issues, parties, refund, actions | Không đọc CSV trực tiếp hoặc bịa evidence |
| Verifier | Candidate output and expected source IDs | Pass/fail | Không sửa im lặng output không hợp lệ |

## Handoff flow

1. Coordinator kiểm tra `policy_version` và `claimed_order_id`.
2. Customer và Order/Product tạo handoff độc lập từ source rows. Payment và Delivery dùng item handoff để tránh join lại không nhất quán.
3. Policy áp dụng đúng thứ tự ưu tiên của `EC_POLICY_V2`: canceled, unavailable, late seller, late logistics, valid split payment, unsupported claim. Secondary issues và action được thêm theo thứ tự đề bài.
4. Coordinator hợp nhất result, dựng evidence IDs từ ID có thật, sau đó gửi Verifier.
5. Verifier kiểm tra claimed order, limits, confidence, format/uniqueness evidence và prefix của item/payment IDs. Chỉ case qua verifier mới được ghi vào staging output.
6. Sau khi cả 50 case đạt, staging được chuyển vào `output/`; trace của lần chạy mới ghi đè `logging/trace.jsonl`.

## Runtime and reproducibility

- Entry point: `python run.py`
- Framework: Python 3 standard library; không cần API key hay mô hình ngoài.
- Decimal được dùng cho tổng BRL và sai số `0.10`, sau đó làm tròn hai chữ số.
- Thứ tự mảng lấy từ thứ tự row nguồn; các danh sách unique giữ lần xuất hiện đầu tiên.
- Một lượt chạy đòi hỏi đúng `EC_001.json` … `EC_050.json`; thiếu hoặc dư case là lỗi chặn để tránh submission không hoàn chỉnh.
