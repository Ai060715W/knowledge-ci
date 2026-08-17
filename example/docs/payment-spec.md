# 支付规范 / Payment Spec

## 重试 / Retry

支付重试上限为 3 次，超过后触发补偿流程。第三方网关的扣款幂等性仅在 3 次内保证。

The payment retry limit is 3; compensation runs afterwards. Gateway idempotency
is only guaranteed within 3 attempts.

## 退款 / Refund

退款必须走幂等通道：同一退款单号重复提交不会二次打款；退款金额不得超过原订单实付金额。

Refunds must use the idempotent channel: the same refund id never pays twice,
and the amount must not exceed the original paid amount.
