# Scoring

1. **Row weight.** A row weighs its total insured value (BuildingTIV + OtherTIV + ContentsTIV + BITIV). A row with
   none weighs the median value of the rows of its currency, so a missing value still counts. With several
   currencies, the rows of each currency share a weight equal to their number of rows.
2. **Penalty.** A flagged row takes, for that sub-code, a penalty of `factor x weight / 3`, between 0 and 1. The
   factor is 1 for critical, 0.5 for warning, 0.1 for info; the weight (0 to 3) is in [RULES.md](../RULES.md).
3. **Within a family** (WHERE, WHAT, VALUE, LINK, and the secondary attributes of WHAT-012), a row keeps its
   largest penalty: two missing characteristics do not count twice.
4. **Across families**, penalties combine like independent failures: a row keeps
   `(1 - p_WHERE)(1 - p_WHAT)(1 - p_VALUE)(1 - p_LINK)(1 - p_secondary)` of its weight.
5. **Score** = 100 x the weight kept / the total weight, rounded. **Grade**: A from 90, B from 80, C from 70,
   D from 55, else E.

Each family also gets its own score, 100 x (1 - its weighted mean penalty over the rows its rules evaluated), for
diagnosis. Issues are ranked by `share of value x penalty`: what they cost the score, at most.

The weight of a sub-code is the mean, rounded, of its five peril weights in sov2oed 0.x (wind, flood, earthquake,
fire, hail). These are judgments, published so that they can be challenged.
