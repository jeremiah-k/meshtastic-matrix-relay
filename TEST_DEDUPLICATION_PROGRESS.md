# Test Deduplication Progress

## Overview

This document tracks removal of duplicate tests from the monolithic
`tests/test_matrix_utils.py` after the refactor in commit fb1e09c that split
tests into modular files.

Date: 2025-12-31

## Task List

- [x] Identify duplicate tests between the monolithic file and modular files
      (auth/bot/core/errors/room).
- [x] Remove duplicate tests from `tests/test_matrix_utils.py`.
- [x] Verify duplicate test names no longer exist in the monolithic file.
- [ ] Run pytest to validate (pending).

## Status Summary

- Monolithic file line count: 9,654 -> 6,804 (approx 2,850 lines removed, ~29.5%
  reduction).
- Duplicate categories removed from monolithic: auth, bot, core, errors, room.
- Remaining tests in `tests/test_matrix_utils.py` are integration or unique
  cases only.

## Remaining Coverage in `tests/test_matrix_utils.py`

These are intentionally kept because they are not covered by modular files:

- on_room_message integration variants (relay logic, reactions, replies,
  detection sensor handling).
- matrix_relay integration tests.
- image/upload and reply-to-meshtastic tests.
- display name, detection sensor packet handling, room member/decryption tests.
- helper tests for `_get_msgs_to_keep_config` and `create_mapping_info`.

## Verification Notes

- Duplicate-name scan shows zero overlaps between `tests/test_matrix_utils.py`
  and:
  - `tests/test_matrix_utils_auth.py`
  - `tests/test_matrix_utils_bot.py`
  - `tests/test_matrix_utils_core.py`
  - `tests/test_matrix_utils_errors.py`
  - `tests/test_matrix_utils_room.py`
