# Audit evaluation: Infosys

Pitch `6746971306` (recommended ABHI), policies NIVA, ABHI, CARE, HDFC. Run 2026-09-27 11:34.

Known errors were planted into copies of a real generated pitch, one per claim, and the copies were audited. **Caught** = FAIL (the deck can't be sent until the claim is fixed or removed). **Flagged** = FAIL or REVIEW (a human must look before the deck can be sent). **Missed** = VERIFIED (the error would reach the client). Harmless rewordings measure false alarms.

## Summary

| Mode | Errors caught (FAIL) | Errors flagged (FAIL or REVIEW) | Missed | False alarms on harmless rewordings | False alarms on untouched claims |
|---|---|---|---|---|---|
| code only | 15/20 (75%) | 20/20 (100%) | 0 | 0/3 | 0/43 |
| code+AI | 17/20 (85%) | 20/20 (100%) | 0 | 0/3 | 0/43 |

## By error type

| Error type | Expected to be caught by | code only | code+AI |
|---|---|---|---|
| number changed | code | 3/3 caught | 3/3 caught |
| number added | code | 2/2 caught | 2/2 caught |
| qualifier dropped | code | 2/2 caught | 2/2 caught |
| wrong insurer | code | 2/2 caught | 2/2 caught |
| company number | code | 1/1 caught | 1/1 caught |
| score changed | code | 1/1 caught | 1/1 caught |
| fabricated detail | AI meaning check | 0/2 caught, 2 to review | 0/2 caught, 2 to review |
| negation | AI meaning check | 0/2 caught, 2 to review | 2/2 caught |
| company year | code | 1/1 caught | 1/1 caught |
| swapped citation | AI meaning check | 1/1 caught | 1/1 caught |
| uncited invention | AI meaning check | 2/2 caught | 2/2 caught |
| exaggeration | AI meaning check | 0/1 caught, 1 to review | 0/1 caught, 1 to review |

## Every planted change (code+AI)

| # | Type | Claim | Planted text | Result | Caught by / reason |
|---|---|---|---|---|---|
| 1 | number changed | S3-1 | Address sedentary risks with HealthReturns = up to 200%, Advanced Health Checkup - Activ One SAVR Plan = at no | **FAIL** | code: numbers |
| 2 | number changed | S3-2 | Support young families with Maternity Cover (Domestic) - Activ One VIP+ plan = up to INR 3 Lac [plan_dependent | **FAIL** | code: numbers |
| 3 | number changed | S3-4 | Manage travel risks with Global Cover / Enhanced = Covered [plan_dependent] and Maternity Cover (Worldwide) -  | **FAIL** | code: numbers |
| 4 | number added | S3-3 | Cover older family members smoothly as Maximum Entry Age = No maximum capping, up to ₹25 lakh per year. | **FAIL** | code: numbers |
| 5 | number added | S5-2 | It addresses lifestyle and chronic conditions effectively via HealthReturns = up to 100% and Day 1 Chronic Con | **FAIL** | code: numbers |
| 6 | qualifier dropped | S4-3 | 100% | **FAIL** | code: qualifier |
| 7 | qualifier dropped | S4-7 | Up to 6 times (6x) | **FAIL** | code: qualifier |
| 8 | wrong insurer | S4-1 | Under Niva Bupa ReAssure 2.0, up to SI | **FAIL** | code: attribution |
| 9 | wrong insurer | S4-2 | Under Niva Bupa ReAssure 2.0, at actuals | **FAIL** | code: attribution |
| 10 | company number | S1-1 | Infosys operates in the IT service management industry, headquartered in Bengaluru, India since 3,962 with a m | **FAIL** | code: numbers |
| 11 | score changed | S1-2 | Based on industry norms and company scale, the workforce likely consists predominantly of young-to-middle-aged | **FAIL** | code: numbers |
| 12 | fabricated detail | S5-3 | It caters to diverse employee demographics through Maximum Entry Age = No maximum capping and Global Cover / E | **REVIEW** | AI meaning check |
| 13 | negation | S4-4 | Unlimited Automatic Recharge is not covered by this policy. | **FAIL** | AI meaning check |
| 14 | negation | S4-5 | Automatic Restore Benefit is not covered by this policy. | **FAIL** | AI meaning check |
| 15 | number reformat (harmless) | S4-12 | Up to INR 5 lakh | **VERIFIED** | All checks passed |
| 16 | synonym (harmless) | S4-8 | 50% of SI per year, max. a maximum of 100% of SI | **VERIFIED** | All checks passed |
| 17 | synonym (harmless) | S4-10 | maximum a maximum of 5 times of Base Sum Insured (on select plans) | **VERIFIED** | All checks passed |
| 18 | company year | S1-1 | Infosys operates in the IT service management industry, headquartered in Bengaluru, India since 1975, with a m | **FAIL** | code: numbers |
| 19 | fabricated detail | S3-1 | Address sedentary risks with HealthReturns = up to 100%, Advanced Health Checkup - Activ One SAVR Plan = at no | **REVIEW** | AI meaning check |
| 20 | swapped citation | S3-2 | Claim Protect (Non-medical expenses cover): 100%. | **FAIL** | code: numbers, qualifier |
| 21 | uncited invention | S3-3 | Includes free international travel insurance for the whole family. | **FAIL** | AI meaning check |
| 22 | uncited invention | S3-4 | Pays a wellness allowance of ₹20,000 a year for gym memberships. | **FAIL** | AI meaning check |
| 23 | exaggeration | S5-2 | It addresses lifestyle and chronic conditions effectively via HealthReturns = up to 100% and Day 1 Chronic Con | **REVIEW** | AI meaning check |

Not tested (no suitable claim in this pitch): number_reformat (1 of 2).
