"""A forecaster for the Agents vs Wall Street challenge.

Python writes JSON. The site reads it. There is nothing in between.

Built stdlib-only: there is no working pip on this machine, so the Anthropic
client is our own urllib wrapper over the Messages API rather than the SDK. No
third-party framework, no other provider.
"""
