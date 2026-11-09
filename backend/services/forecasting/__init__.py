"""Demand Forecasting module.

Headless engine that classifies demand patterns, learns delivery day
shares, runs multiple forecast models in parallel, and writes to the
Phase 1 schema (_tb_SOForecast, _tb_SODemandClassification,
_tb_SODeliveryPattern, _tb_SOForecastRun).

Entry points land in Phase 2b (engine.py + runner.py). Phase 2a ships
the pure building blocks: config, week math, history loading with
short-delivery uplift, pattern classification, delivery-day learner,
and the base-median model that replicates the current
history.py:order_suggestions algorithm exactly.
"""
