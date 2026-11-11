# Public invoice dataset evaluation

The external pipeline check uses the Kaggle [Customer Invoices Dataset](https://www.kaggle.com/datasets/pradumn203/payment-date-prediction-for-invoices-dataset), released under CC BY-NC 4.0. The source CSV is not included in this repository. This license is non-commercial; obtain separate rights before using the dataset in a commercial training workflow.

## Reproduce

Download the CSV from Kaggle, then run:

```sh
make evaluate-public PUBLIC_INVOICE_CSV=/path/to/customer_invoices.csv
```

The evaluator hashes invoice and customer identifiers in memory, removes invalid rows and conflicting duplicate invoice IDs, and writes aggregate metrics to `artifacts/public_eval.json`. It does not export names, IDs, or row-level records. Cox and pooled Kaplan–Meier use a deterministic SHA-256 sample stratified across a temporal train/test split. All cleaned invoices remain available to construct point-in-time client-history features. The production daily LightGBM hazard recipe runs on a second deterministic 1,000-invoice sample because its invoice-day risk-row expansion is substantially more expensive. Every comparator uses the project's IPCW survival metrics. This is an independent pipeline check, not a prospective result for the synthetic-trained deployed model.

## Recorded result

The recorded run used 50,000 source rows. Cleaning retained 48,702 unique invoices: 39,048 fully paid and 9,654 open/right-censored invoices. It removed 140 invalid or anomalous rows; no conflicting invoice-ID groups remained. The temporal split trained through 2019-11-24 and tested on later invoices, with observation ending 2020-05-22. A deterministic 5,000-invoice sample produced 3,265 training invoices and 1,735 test invoices. The test set contained 745 paid events and 990 right-censored invoices.

| Refit comparator | Test invoices (paid / censored) | Uno concordance | Integrated Brier score | Due-date AUC |
| --- | ---: | ---: | ---: | ---: |
| Pooled Kaplan–Meier | 1,735 (745 / 990) | 0.500 | 0.1411 | 0.541 |
| Cox proportional hazards | 1,735 (745 / 990) | 0.624 | 0.1428 | 0.696 |
| Daily LightGBM hazard | 347 (149 / 198) | 0.650 | 0.1451 | 0.730 |

The LightGBM test metrics use its smaller 1,000-invoice temporal sample (653 train, 347 test), so compare them as an external pipeline check rather than as a same-cohort model ranking. These results describe this historical sample and its collection process only. The source contains USD and CAD invoices and differs from the synthetic demonstration cohort. The detailed aggregate metrics, including Brier scores at each horizon and AUC at due date plus 14/30 days, are in [`artifacts/public_eval.json`](../artifacts/public_eval.json).
