"""Forecast model implementations.

Each model exposes a pure `forecast()` function that takes an in-memory
weekly history dict and returns either a prediction or None. All DB
plumbing (loading history, writing predictions) lives one level up in
services/forecasting; models themselves are pure so they're easy to
unit test and swap out.
"""
