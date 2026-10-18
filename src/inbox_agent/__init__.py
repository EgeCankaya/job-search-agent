"""Runtime code for the inbox agent: ingestion, classification and the tracker.

Nothing here talks to Gmail or Bedrock directly. Each outside service sits
behind a small interface, so the real client plugs in as a config change
(MODE-1) and tests use fakes.
"""
