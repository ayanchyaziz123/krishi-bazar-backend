# AI inventory insights: planned API

The admin dashboard has an **AI inventory insights** panel
(`frontend/src/admin_components/AIInsights.js`). Until a forecasting service exists it
shows a "Not set up yet" state. It never shows invented predictions.

To switch it on, add this endpoint and pass its `insights` list to the component.

## `GET /api/ai/insights/`

Managers only (`IsManager`). Returns the latest stored analysis; it should not run a
model on every request.

```json
{
  "generated_at": "2026-10-14T06:00:00Z",
  "insights": [
    {
      "id": "stockout-113",
      "kind": "stockout",
      "product": { "id": 113, "name": "Chickpeas (Kabuli Chola)", "unit": "piece" },
      "risk": "critical",
      "headline": "Chickpeas expected to run out in 2 days",
      "explanation": "Sold 18 a day over the last 14 days; 36 left on the shelf.",
      "stockout_date": "2026-10-16",
      "suggested_qty": 200,
      "action": { "label": "Review reorder", "to": "/admin/receive" }
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `kind` | `stockout`, `reorder`, `expiry`, `anomaly` or `purchase` |
| `risk` | `critical`, `high`, `medium` or `low` (sets the badge colour) |
| `headline` | One short sentence, shown in bold |
| `explanation` | Why the recommendation was made, in plain words |
| `stockout_date` | Predicted date stock reaches zero, or `null` |
| `suggested_qty` | Suggested reorder amount in the product's unit, or `null` |
| `action` | Where the "review" link goes, or `null` for information only |

## Data already available for forecasting

- `StockMovement`: every sale (counter and online), delivery, wastage and correction, with time and quantity.
- `PurchaseItem`: each batch, its remaining quantity, cost and expiry date.
- `Product.reorder_level`: the warning level each product already has.

A simple first version: average daily sales over 14–28 days per product, divide current
stock by it for days left, and flag batches whose expiry comes before they would sell out.
