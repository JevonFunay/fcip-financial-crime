# Validation-only error audit

Explicit synthetic intervention labels; zero labels are assumed background/lookalikes, not verified legitimate banking activity.

One account-week; stress takes priority over lookalike over background. Multiple same-priority recipes become mixed_recipes. Counts never duplicate a week.

Associations below do not establish causal feature importance. Source flags remain review load only.

## AML selected

Units 20417; TP 53, FP 699, FN 69, TN 19596.

| Recipe | FP | FN | Zero-label flag rate | Positive-label recall |
|---|---:|---:|---:|---:|
| dormant_reactivation | 0 | 27 | n/a | 10.00% |
| micro_splitting | 0 | 20 | n/a | 16.67% |
| pass_through_proxy | 0 | 16 | n/a | 27.27% |
| session_change | 0 | 6 | n/a | 76.92% |
| ordinary | 557 | 0 | 2.78% | n/a |
| new_phone | 52 | 0 | 72.22% | n/a |
| travel | 49 | 0 | 81.67% | n/a |
| shopping | 31 | 0 | 63.27% | n/a |
| repeated_small_payments | 10 | 0 | 17.86% | n/a |
| card_testing_burst | 0 | 0 | n/a | 100.00% |

## AML reference

Units 20417; TP 66, FP 2061, FN 56, TN 18234.

| Recipe | FP | FN | Zero-label flag rate | Positive-label recall |
|---|---:|---:|---:|---:|
| micro_splitting | 0 | 20 | n/a | 16.67% |
| dormant_reactivation | 0 | 16 | n/a | 46.67% |
| pass_through_proxy | 0 | 16 | n/a | 27.27% |
| session_change | 0 | 4 | n/a | 84.62% |
| ordinary | 1906 | 0 | 9.50% | n/a |
| new_phone | 58 | 0 | 80.56% | n/a |
| travel | 50 | 0 | 83.33% | n/a |
| shopping | 34 | 0 | 69.39% | n/a |
| repeated_small_payments | 13 | 0 | 23.21% | n/a |
| card_testing_burst | 0 | 0 | n/a | 100.00% |

## FRAUD selected

Units 200000; TP 374, FP 7153, FN 1589, TN 190884.

| Recipe | FP | FN | Zero-label flag rate | Positive-label recall |
|---|---:|---:|---:|---:|
| micro_splitting | 0 | 376 | n/a | 4.57% |
| card_testing_burst | 0 | 373 | n/a | 15.23% |
| pass_through_proxy | 0 | 321 | n/a | 8.55% |
| session_change | 0 | 271 | n/a | 29.06% |
| dormant_reactivation | 0 | 248 | n/a | 37.37% |
| ordinary | 6558 | 0 | 3.38% | n/a |
| new_phone | 225 | 0 | 24.14% | n/a |
| shopping | 195 | 0 | 18.55% | n/a |
| travel | 118 | 0 | 11.22% | n/a |
| repeated_small_payments | 57 | 0 | 5.82% | n/a |

## FRAUD reference

Units 200000; TP 308, FP 13030, FN 1655, TN 185007.

| Recipe | FP | FN | Zero-label flag rate | Positive-label recall |
|---|---:|---:|---:|---:|
| micro_splitting | 0 | 372 | n/a | 5.58% |
| card_testing_burst | 0 | 350 | n/a | 20.45% |
| dormant_reactivation | 0 | 331 | n/a | 16.41% |
| pass_through_proxy | 0 | 308 | n/a | 12.25% |
| session_change | 0 | 294 | n/a | 23.04% |
| ordinary | 12356 | 0 | 6.37% | n/a |
| shopping | 336 | 0 | 31.97% | n/a |
| new_phone | 146 | 0 | 15.67% | n/a |
| travel | 111 | 0 | 10.55% | n/a |
| repeated_small_payments | 81 | 0 | 8.27% | n/a |

See slices.csv for amount, history/reference support, device/context/login and timestamp-precision partitions.
No model fitting, threshold selection, final-data access, promotion or database operation occurred.
