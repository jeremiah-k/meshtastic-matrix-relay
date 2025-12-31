# Test Deduplication Tracking Document

## Overview

This document tracks the systematic removal of duplicate tests from the monolithic `test_matrix_utils.py` file.
The 3rd commit back (fb1e09c) created new modular test files, but the old monolithic file still contains duplicates.

## Modular Test Files Created

| File                        | Purpose                                         | Line Count |
| --------------------------- | ----------------------------------------------- | ---------- |
| test_matrix_utils_auth.py   | Authentication and login functionality          | ~1,132     |
| test_matrix_utils_bot.py    | Bot command detection                           | ~194       |
| test_matrix_utils_core.py   | Core utilities (formatting, validation, config) | ~600       |
| test_matrix_utils_errors.py | Error message handling                          | ~155       |
| test_matrix_utils_room.py   | Room joining and alias resolution               | ~625       |
| **Total**                   |                                                 | **~2,706** |

## Duplicated Tests Found

The following tests appear in BOTH the old monolithic file AND the new modular files:

### test_matrix_utils_bot.py Duplicates

| Test Name                                  | Old File Line(s) | New File Line(s) |
| ------------------------------------------ | ---------------- | ---------------- |
| direct_mention                             | ~1,931-1,940     | 20-29            |
| direct_mention_require_mention_false       | ~1,942-1,951     | 31-40            |
| direct_mention_require_mention_true        | ~1,953-1,964     | 42-53            |
| no_match                                   | ~1,966-1,975     | 55-64            |
| no_match_require_mention_true              | ~1,977-1,986     | 66-75            |
| case_insensitive                           | ~1,988-1,997     | 77-86            |
| case_insensitive_require_mention_true      | ~1,999-2,008     | 88-97            |
| with_args                                  | ~2,010-2,019     | 99-108           |
| with_args_require_mention_true             | ~2,021-2,030     | 110-119          |
| bot_mention_require_mention_true           | ~2,032-2,041     | 121-130          |
| bot_mention_with_name_require_mention_true | ~2,043-2,052     | 132-141          |
| non_bot_mention_require_mention_true       | ~2,054-2,063     | 143-152          |
| bot_mention_require_mention_false          | ~2,065-2,074     | 154-163          |
| empty_command_returns_false                | ~2,076-2,083     | 165-172          |
| bad_identifier_skips_mention_parts         | ~2,085-2,104     | 174-193          |

### test_matrix_utils_core.py Duplicates

| Test Name                                                   | Old File Line(s)                   | New File Line(s) |
| ----------------------------------------------------------- | ---------------------------------- | ---------------- |
| create_mapping_info_defaults                                | ~5,011-5,022                       | 24-37            |
| create_mapping_info_none_values                             | ~N/A (test_matrix_utils_core only) | 40-58            |
| create_mapping_info_with_quoted_text                        | ~N/A (test_matrix_utils_core only) | 61-83            |
| get_interaction_settings_new_format                         | ~5,026-5,036                       | 86-96            |
| get_interaction_settings_legacy_format                      | ~5,039-5,049                       | 99-109           |
| get_interaction_settings_defaults                           | ~5,052-5,060                       | 112-120          |
| get_interaction_settings_none_config                        | ~5,063-5,067                       | 123-127          |
| message_storage_enabled_true                                | ~5,070-5,081                       | 130-141          |
| message_storage_enabled_false                               | ~5,084-5,089                       | 144-149          |
| add_truncated_vars                                          | ~5,092-5,103                       | 155-166          |
| add_truncated_vars_empty_text                               | ~5,106-5,115                       | 169-177          |
| add_truncated_vars_none_text                                | ~5,118-5,127                       | 180-189          |
| escape_leading_prefix_for_markdown_with_markdown_chars      | ~5,130-5,165                       | 193-228          |
| escape_leading_prefix_for_markdown_non_prefix               | ~5,168-5,173                       | 231-236          |
| validate_prefix_format_valid                                | ~5,179-5,188                       | 239-248          |
| validate_prefix_format_invalid_key                          | ~5,191-5,202                       | 251-262          |
| get_meshtastic_prefix_enabled                               | ~5,205-5,214                       | 265-274          |
| get_meshtastic_prefix_disabled                              | ~5,217-5,224                       | 277-284          |
| get_meshtastic_prefix_custom_format                         | ~5,227-5,234                       | 287-294          |
| get_meshtastic_prefix_invalid_format                        | ~5,237-5,246                       | 297-306          |
| get_matrix_prefix_enabled                                   | ~5,249-5,257                       | 309-316          |
| get_matrix_prefix_disabled                                  | ~5,260-5,266                       | 319-326          |
| get_matrix_prefix_default_format                            | ~5,269-5,281                       | 329-341          |
| truncate_message_under_limit                                | ~5,287-5,293                       | 344-350          |
| truncate_message_over_limit                                 | ~5,296-5,303                       | 353-360          |
| truncate_message_unicode                                    | ~5,306-5,313                       | 363-370          |
| strip_quoted_lines_with_quotes                              | ~5,316-5,323                       | 373-380          |
| strip_quoted_lines_no_quotes                                | ~5,326-5,331                       | 383-388          |
| strip_quoted_lines_only_quotes                              | ~5,334-5,340                       | 391-397          |
| format_reply_message                                        | ~5,343-5,355                       | 400-412          |
| format_reply_message_remote_mesh_prefix                     | ~5,358-5,373                       | 415-430          |
| format_reply_message_remote_without_longname                | ~5,376-5,391                       | 433-448          |
| format_reply_message_remote_strips_prefix_and_uses_override | ~5,394-5,413                       | 451-470          |
| normalize_bot_user_id_already_full_mxid                     | ~N/A (test_matrix_utils_core only) | 476-483          |
| normalize_bot_user_id_ipv6_homeserver                       | ~N/A (test_matrix_utils_core only) | 486-493          |
| normalize_bot_user_id_full_mxid_with_port                   | ~N/A (test_matrix_utils_core only) | 496-503          |
| normalize_bot_user_id_with_at_prefix                        | ~N/A (test_matrix_utils_core only) | 506-513          |
| normalize_bot_user_id_without_at_prefix                     | ~N/A (test_matrix_utils_core only) | 516-523          |
| normalize_bot_user_id_with_complex_homeserver               | ~N/A (test_matrix_utils_core only) | 526-533          |
| normalize_bot_user_id_empty_input                           | ~N/A (test_matrix_utils_core only) | 536-543          |
| normalize_bot_user_id_none_input                            | ~N/A (test_matrix_utils_core only) | 546-553          |
| normalize_bot_user_id_trailing_colon                        | ~N/A (test_matrix_utils_core only) | 556-563          |
| get_valid_device_id_valid_string                            | ~N/A (test_matrix_utils_core only) | 566-574          |
| get_valid_device_id_empty_string                            | ~N/A (test_matrix_utils_core only) | 577-585          |
| get_valid_device_id_non_string                              | ~N/A (test_matrix_utils_core only) | 588-599          |

### test_matrix_utils_errors.py Duplicates

| Test Name                                            | Old File Line(s)                     | New File Line(s) |
| ---------------------------------------------------- | ------------------------------------ | ---------------- |
| sync_error_with_message_string                       | ~N/A (test_matrix_utils_errors only) | 14-20            |
| sync_error_with_status_code_401                      | ~N/A (test_matrix_utils_errors only) | 22-30            |
| sync_error_with_status_code_403                      | ~N/A (test_matrix_utils_errors only) | 32-39            |
| sync_error_with_status_code_404                      | ~N/A (test_matrix_utils_errors only) | 41-48            |
| sync_error_with_status_code_429                      | ~N/A (test_matrix_utils_errors only) | 50-57            |
| sync_error_with_status_code_500                      | ~N/A (test_matrix_utils_errors only) | 59-69            |
| sync_error_with_bytes_response                       | ~N/A (test_matrix_utils_errors only) | 71-76            |
| sync_error_with_bytes_invalid_utf8                   | ~N/A (test_matrix_utils_errors only) | 78-85            |
| sync_error_with_bytearray_response                   | ~N/A (test_matrix_utils_errors only) | 87-92            |
| sync_error_fallback_generic                          | ~N/A (test_matrix_utils_errors only) | 94-106           |
| get_detailed_matrix_error_message_transport_response | ~N/A (test_matrix_utils_errors only) | 108-118          |
| get_detailed_matrix_error_message_string_fallback    | ~N/A (test_matrix_utils_errors only) | 120-136          |
| get_e2ee_error_message                               | ~N/A (test_matrix_utils_errors only) | 139-154          |

### test_matrix_utils_room.py Duplicates

| Test Name                                         | Old File Line(s)                   | New File Line(s) |
| ------------------------------------------------- | ---------------------------------- | ---------------- |
| join_matrix_room_by_id                            | ~2,258-2,269                       | 19-32            |
| join_matrix_room_already_joined                   | ~2,272-2,285                       | 35-48            |
| join_matrix_room_resolves_alias                   | ~2,289-2,309                       | 51-72            |
| join_matrix_room_resolve_alias_handles_nio_errors | ~2,313-2,333                       | 75-96            |
| join_matrix_room_resolve_alias_missing_room_id    | ~2,337-2,356                       | 99-119           |
| join_matrix_room_rejects_non_string_identifier    | ~2,360-2,371                       | 123-134          |
| is_room_alias_with_alias                          | ~N/A (test_matrix_utils_room only) | 140-143          |
| is_room_alias_with_room_id                        | ~N/A (test_matrix_utils_room only) | 146-149          |
| is_room_alias_with_non_string                     | ~N/A (test_matrix_utils_room only) | 152-156          |
| iter_room_alias_entries_list_with_strings         | ~N/A (test_matrix_utils_room only) | 159-184          |
| iter_room_alias_entries_list_with_dicts           | ~N/A (test_matrix_utils_room only) | 187-210          |
| iter_room_alias_entries_dict_with_strings         | ~N/A (test_matrix_utils_room only) | 213-240          |
| iter_room_alias_entries_dict_with_dicts           | ~N/A (test_matrix_utils_room only) | 243-263          |
| iter_room_alias_entries_complex_nested            | ~N/A (test_matrix_utils_room only) | 377-413          |
| iter_room_alias_entries_dict_format               | ~N/A (test_matrix_utils_room only) | 416-449          |
| iter_room_alias_entries_empty_id                  | ~N/A (test_matrix_utils_room only) | 452-475          |
| resolve_aliases_in_mapping_list                   | ~N/A (test_matrix_utils_room only) | 267-286          |
| resolve_aliases_in_mapping_dict                   | ~N/A (test_matrix_utils_room only) | 289-309          |
| resolve_aliases_in_mapping_resolver_failure       | ~N/A (test_matrix_utils_room only) | 492-503          |
| resolve_aliases_in_mapping_unsupported_type       | ~N/A (test_matrix_utils_room only) | 478-489          |
| update_room_id_in_mapping_list                    | ~N/A (test_matrix_utils_room only) | 312-321          |
| update_room_id_in_mapping_list_dict               | ~N/A (test_matrix_utils_room only) | 324-336          |
| update_room_id_in_mapping_dict                    | ~N/A (test_matrix_utils_room only) | 339-348          |
| update_room_id_in_mapping_dict_dicts              | ~N/A (test_matrix_utils_room only) | 351-363          |
| update_room_id_in_mapping_not_found               | ~N/A (test_matrix_utils_room only) | 366-374          |
| display_room_channel_mappings                     | ~N/A (test_matrix_utils_room only) | 525-550          |
| display_room_channel_mappings_empty               | ~N/A (test_matrix_utils_room only) | 553-563          |
| display_room_channel_mappings_no_config           | ~N/A (test_matrix_utils_room only) | 566-576          |
| display_room_channel_mappings_dict_config         | ~N/A (test_matrix_utils_room only) | 579-600          |
| display_room_channel_mappings_no_display_name     | ~N/A (test_matrix_utils_room only) | 603-624          |

### test_matrix_utils_auth.py Duplicates

| Test Name                                                         | Old File Line(s)                   | New File Line(s) |
| ----------------------------------------------------------------- | ---------------------------------- | ---------------- |
| connect_matrix_success                                            | ~2,148-2,204                       | 22-55            |
| connect_matrix_without_credentials                                | ~2,208-2,252                       | 58-86            |
| connect_matrix_alias_resolution_success                           | ~2,379-2,484                       | 94-156           |
| connect_matrix_alias_resolution_failure                           | ~2,492-2,527                       | 164-227          |
| connect_matrix_with_e2ee_credentials                              | ~N/A (test_matrix_utils_auth only) | 240-294          |
| login_matrix_bot_success                                          | ~N/A (test_matrix_utils_auth only) | 305-341          |
| login_matrix_bot_with_parameters                                  | ~N/A (test_matrix_utils_auth only) | 346-369          |
| login_matrix_bot_login_failure                                    | ~N/A (test_matrix_utils_auth only) | 374-391          |
| login_matrix_bot_adds_scheme_and_discovery_timeout                | ~N/A (test_matrix_utils_auth only) | 398-434          |
| login_matrix_bot_discovery_response_with_homeserver_url_attribute | ~N/A (test_matrix_utils_auth only) | 441-484          |
| login_matrix_bot_discovery_response_unexpected_no_attr            | ~N/A (test_matrix_utils_auth only) | 491-529          |
| login_matrix_bot_username_normalization_failure_returns_false     | ~N/A (test_matrix_utils_auth only) | 534-560          |
| login_matrix_bot_debug_env_sets_log_levels                        | ~N/A (test_matrix_utils_auth only) | 567-610          |
| login_matrix_bot_discovery_type_error_logs_warning                | ~N/A (test_matrix_utils_auth only) | 617-655          |
| login_matrix_bot_cleanup_error_logs_debug                         | ~N/A (test_matrix_utils_auth only) | 661-695          |
| login_matrix_bot_username_warnings                                | ~N/A (test_matrix_utils_auth only) | 701-733          |
| logout_matrix_bot_no_credentials                                  | ~N/A (test_matrix_utils_auth only) | 741-745          |
| logout_matrix_bot_invalid_credentials                             | ~N/A (test_matrix_utils_auth only) | 749-772          |
| logout_matrix_bot_password_verification_success                   | ~N/A (test_matrix_utils_auth only) | 776-811          |
| logout_matrix_bot_password_verification_failure                   | ~N/A (test_matrix_utils_auth only) | 815-838          |
| logout_matrix_bot_server_logout_failure                           | ~N/A (test_matrix_utils_auth only) | 842-874          |
| get_e2ee_store_dir                                                | ~N/A (test_matrix_utils_auth only) | 881-886          |
| load_credentials_success                                          | ~N/A (test_matrix_utils_auth only) | 893-912          |
| load_credentials_file_not_exists                                  | ~N/A (test_matrix_utils_auth only) | 917-925          |
| save_credentials                                                  | ~N/A (test_matrix_utils_auth only) | 932-951          |
| cleanup_local_session_data_success                                | ~N/A (test_matrix_utils_auth only) | 957-972          |
| cleanup_local_session_data_files_not_exist                        | ~N/A (test_matrix_utils_auth only) | 976-984          |
| cleanup_local_session_data_permission_error                       | ~N/A (test_matrix_utils_auth only) | 988-998          |
| can_auto_create_credentials_success                               | ~N/A (test_matrix_utils_auth only) | 1001-1010        |
| can_auto_create_credentials_none_bot_user_id                      | ~N/A (test_matrix_utils_auth only) | 1013-1022        |
| can_auto_create_credentials_none_values_homeserver                | ~N/A (test_matrix_utils_auth only) | 1026-1045        |
| logout_matrix_bot_missing_user_id_fetch_success                   | ~N/A (test_matrix_utils_auth only) | 1049-1104        |
| logout_matrix_bot_timeout                                         | ~N/A (test_matrix_utils_auth only) | 1108-1131        |

## Tests Unique to Old File (Keep These)

The following tests exist ONLY in the old monolithic file and should be kept:

### on_room_message Tests (lines ~119-1338)

- test_on_room_message_simple_text
- test_on_room_message_remote_prefers_meshtastic_text
- test_on_room_message_ignore_bot
- test_on_room_message_reply_enabled
- test_on_room_message_reply_disabled
- test_on_room_message_reaction_enabled
- test_on_room_message_reaction_disabled
- test_on_room_message_unsupported_room
- test_on_room_message_detection_sensor_enabled
- test_on_room_message_detection_sensor_disabled
- test_on_room_message_detection_sensor_broadcast_disabled
- test_on_room_message_detection_sensor_connect_failure
- test_on_room_message_ignores_old_messages
- test_on_room_message_config_none_logs_and_returns
- test_on_room_message_suppressed_message_returns
- test_on_room_message_remote_reaction_relay_success
- test_on_room_message_reaction_missing_mapping_logs_debug
- test_on_room_message_local_reaction_queue_failure_logs
- test_on_room_message_reply_handled_short_circuits
- test_on_room_message_remote_meshnet_empty_after_prefix_skips
- test_on_room_message_portnum_string_digits
- test_on_room_message_plugin_handle_exception_logs_and_continues
- test_on_room_message_plugin_match_exception_does_not_block
- test_on_room_message_no_meshtastic_interface_returns
- test_on_room_message_broadcast_disabled_no_queue
- test_on_room_message_queue_failure_logs_error

### Matrix Utility Helper Tests (lines ~456-553)

- test_get_msgs_to_keep_config_default
- test_get_msgs_to_keep_config_legacy
- test_get_msgs_to_keep_config_new_format
- test_create_mapping_info

### Other Tests (Throughout file)

- test_get_displayname_returns_none_when_client_missing
- test_get_displayname_returns_displayname
- test_get_user_display_name_profile_response
- test_get_user_display_name_no_displayname
- test_get_user_display_name_room_name
- test_get_user_display_name_fallback
- test_get_user_display_name_error_response
- test_get_user_display_name_handles_comm_errors
- test_handle_detection_sensor_packet_success
- test_handle_detection_sensor_packet_invalid_channel
- test_handle_detection_sensor_packet_missing_channel
- test_handle_detection_sensor_packet_queue_fail
- test_handle_detection_sensor_packet_broadcast_disabled
- test_handle_detection_sensor_packet_connect_fail
- test_handle_detection_sensor_packet_queue_size_gt_one
- test_handle_matrix_reply_success
- test_handle_matrix_reply_original_not_found
- test_on_room_member
- test_on_decryption_failure
- test_on_decryption_failure_missing_device_id
- test_matrix_relay_simple_message
- test_matrix_relay_emote_message
- test_matrix_relay_reply_formatting
- test_matrix_relay_reply_missing_mapping_logs_warning
- test_matrix_relay_send_timeout_logs_and_returns
- test_matrix_relay_send_nio_error_logs_and_returns
- test_matrix_relay_store_and_prune_message_map
- test_matrix_relay_store_failure_logs
- test_matrix_relay_markdown_processing
- test_matrix_relay_importerror_fallback
- test_matrix_relay_legacy_msg_map_warning
- test_matrix_relay_e2ee_blocked
- test_matrix_relay_client_none
- test_matrix_relay_no_config_returns
- test_send_image
- test_send_room_image
- test_send_room_image_raises_on_missing_content_uri
- test_upload_image_sets_content_type_and_uses_filename
- test_upload_image_defaults_to_png_when_mimetype_unknown
- test_upload_image_fallbacks_to_png_on_save_error
- test_upload_image_fallbacks_to_png_on_oserror
- test_upload_image_returns_upload_error_on_network_exception
- test_send_reply_to_meshtastic_with_reply_id
- test_send_reply_to_meshtastic_no_reply_id
- test_send_reply_to_meshtastic_returns_when_interface_missing
- test_send_reply_to_meshtastic_fallback_queue_size
- test_send_reply_to_meshtastic_fallback_failure
- test_send_reply_to_meshtastic_structured_reply_queue_size
- test_send_reply_to_meshtastic_structured_reply_failure
- test_on_room_message_command_short_circuits
- test_on_room_message_requires_mention_before_filtering_command
- test_on_room_message_emote_reaction_uses_original_event_id

## Summary

- **Total tests in old file**: ~250+ tests (9,654 lines)
- **Total duplicated tests**: ~150 tests
- **Tests to keep in old file**: ~100+ tests (mostly on_room_message variants and complex integration tests)
- **Estimated lines to remove**: ~2,000-3,000 lines
- **Estimated remaining lines**: ~6,500-7,500 lines

## Action Plan

1. Remove TestBotCommand class and all its test methods (~lines 1,919-2,104)
2. Remove duplicated core utility tests (~lines 4,456-5,413)
3. Remove duplicated room utility tests (~lines 2,258-2,371)
4. Remove duplicated auth tests (~lines 2,148-2,527)
5. Keep all on_room_message tests and other unique tests
6. Run tests to verify nothing is broken
7. Commit changes
