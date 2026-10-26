"""Model lab (framework prototype): map a labelled dataset, train, compare, choose a threshold.

Everything here reuses the existing, tested building blocks: the canonical
normalizer (`app.ml.behavioral.standardize`), the point-in-time FRAUD features
(`app.ml.behavioral.features`) and the JSON Isolation Forest
(`app.ml.behavioral.model`). The lab adds the user-chosen mapping, a label
column, two supervised models, chronological splitting and threshold curves.
"""
