"""
app/harm_estimation
===================
Market-model event-study methodology for harm quantification.

Modules
-------
guards      Hard-coded allow-list: prevents running against live/unlisted instruments.
event_study Market-model OLS regression, AR/CAR, confidence intervals.
trade_harm  Per-trade and per-account harm estimation from counterfactual price path.

Academic basis
--------------
MacKinlay, A.C. (1997). Event Studies in Economics and Finance.
    Journal of Economic Literature, 35(1), 13-39.
Brown, S.J. & Warner, J.B. (1985). Using daily stock returns: The case of
    event studies. Journal of Financial Economics, 14(1), 3-31.
"""
