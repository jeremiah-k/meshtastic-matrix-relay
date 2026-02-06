#!/bin/bash
# Regression guard to prevent reintroduction of legacy environment variables
# and container paths that should not appear in container/K8s/docs surfaces.

set -euo pipefail

PATTERNS=(
	"MMRELAY_CREDENTIALS_PATH"
	"MMRELAY_BASE_DIR"
	"MMRELAY_DATA_DIR"
	"/app/data"
	"--base-dir"
	"--logfile"
)

STRICT_FILES=(
	"Dockerfile"
	"deploy/k8s/pvc.yaml"
	"deploy/k8s/networkpolicy.yaml"
	"deploy/k8s/deployment.yaml"
	"deploy/k8s/kustomization.yaml"
	"deploy/k8s/overlays/digest/kustomization.yaml"
	"Makefile"
	"docs/docker-compose.yml"
	"src/mmrelay/tools/sample-docker-compose.yaml"
	"src/mmrelay/tools/sample-docker-compose-prebuilt.yaml"
	"src/mmrelay/tools/sample.env"
	"deploy/helm/mmrelay/templates/deployment.yaml"
	"deploy/helm/mmrelay/templates/service.yaml"
	"deploy/helm/mmrelay/templates/pvc.yaml"
)

DOC_FILES=(
	"docs/DOCKER.md"
	"docs/INSTRUCTIONS.md"
	"docs/KUBERNETES.md"
	"docs/E2EE.md"
	"docs/ADVANCED_CONFIGURATION.md"
	"docs/README.md"
	"docs/MIGRATION_1.3.md"
	"README.md"
)

ERROR_FOUND=0

echo "Checking for legacy container paths and environment variables..."

# check_strict_files checks each file in STRICT_FILES for any occurrence of the patterns in PATTERNS, prints matching lines (up to five) when found, and sets ERROR_FOUND to 1.
check_strict_files() {
	for PATTERN in "${PATTERNS[@]}"; do
		for FILE in "${STRICT_FILES[@]}"; do
			if [[ -f ${FILE} ]]; then
				MATCHES=$(grep -Fn -- "${PATTERN}" "${FILE}" || true)
				if [[ -n ${MATCHES} ]]; then
					echo "ERROR: Found forbidden pattern '${PATTERN}' in ${FILE}"
					echo "${MATCHES}" | head -5
					echo ""
					ERROR_FOUND=1
				fi
			fi
		done
	done
}

# Marker placement: Place <!-- MMRELAY_ALLOW_LEGACY_EXAMPLE --> immediately ABOVE
# the opening fence (``` or ~~~) of a code block to allow legacy examples.
# The marker applies ONLY to the next fenced block (not the entire document).
#
# Pre-parse each DOC file to collect allowed line ranges from marker-marked blocks.
# check_doc_files scans each file in DOC_FILES for entries from PATTERNS, treats fenced code blocks immediately following `<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->` as allowed ranges, and reports any forbidden pattern occurrences that fall outside those allowed fenced blocks.
check_doc_files() {
	local ALLOW_MARKER="<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->"

	for FILE in "${DOC_FILES[@]}"; do
		if [[ ! -f ${FILE} ]]; then
			continue
		fi

		# Step 1: Collect allowed line ranges from marker-marked blocks
		# Format: start_line,end_line (inclusive of block content, excluding fences)
		local ALLOWED_RANGES=""

		# Read entire file into array for O(n) processing
		local -a FILE_LINES
		mapfile -t FILE_LINES <"${FILE}"
		local TOTAL_LINES=${#FILE_LINES[@]}
		local LINE_IDX=0

		while [[ ${LINE_IDX} -lt ${TOTAL_LINES} ]]; do
			local LINE_CONTENT="${FILE_LINES[LINE_IDX]}"
			local LINE_NUM=$((LINE_IDX + 1))

			# Check if this line is the marker
			if [[ ${LINE_CONTENT} == *"${ALLOW_MARKER}"* ]]; then
				# Look for the next fence opening (allowing blank lines between marker and fence)
				local FENCE_IDX=$((LINE_IDX + 1))
				while [[ ${FENCE_IDX} -lt ${TOTAL_LINES} ]]; do
					local FENCE_CONTENT="${FILE_LINES[FENCE_IDX]}"
					local FENCE_LINE=$((FENCE_IDX + 1))

					# Check for fence opening (``` or ~~~)
					if [[ ${FENCE_CONTENT} =~ ^[[:space:]]*(\`{3}|~{3}) ]]; then
						# Found opening fence - capture the delimiter (``` or ~~~)
						local DELIMITER="${BASH_REMATCH[1]}"

						# Find matching closing fence
						local CLOSING_IDX=$((FENCE_IDX + 1))
						while [[ ${CLOSING_IDX} -lt ${TOTAL_LINES} ]]; do
							local CLOSING_CONTENT="${FILE_LINES[CLOSING_IDX]}"
							local CLOSING_LINE=$((CLOSING_IDX + 1))

							if [[ ${CLOSING_CONTENT} =~ ^[[:space:]]*${DELIMITER}[[:space:]]*$ ]]; then
								# Found the closing fence
								local BLOCK_START=$((FENCE_LINE + 1))
								local BLOCK_END=$((CLOSING_LINE - 1))

								if [[ ${BLOCK_START} -le ${BLOCK_END} ]]; then
									if [[ -n ${ALLOWED_RANGES} ]]; then
										ALLOWED_RANGES="${ALLOWED_RANGES}|"
									fi
									ALLOWED_RANGES="${ALLOWED_RANGES}${BLOCK_START},${BLOCK_END}"
								fi
								break # Exit inner while loop
							fi

							CLOSING_IDX=$((CLOSING_IDX + 1))
						done
						break
					fi

					# Stop searching if we hit another marker or non-blank content
					# Only skip blank lines after marker
					if [[ ${FENCE_LINE} -gt $((LINE_NUM + 10)) ]]; then
						# Safety limit: don't search more than 10 lines after marker
						if [[ ${DEBUG-} == "1" ]]; then
							echo "DEBUG: Marker at line ${LINE_NUM} in ${FILE} has no fence within 10 lines" >&2
						fi
						break
					fi

					# Trim whitespace – if the line is non-blank (and not a
					# fence, which was already handled above), stop searching.
					local FENCE_TRIMMED="${FENCE_CONTENT//[[:space:]]/}"
					if [[ -n ${FENCE_TRIMMED} ]]; then
						break
					fi

					FENCE_IDX=$((FENCE_IDX + 1))
				done
			fi

			LINE_IDX=$((LINE_IDX + 1))
		done

		# Step 2: Check each forbidden pattern match
		for PATTERN in "${PATTERNS[@]}"; do
			MATCHES=$(grep -Fn -- "${PATTERN}" "${FILE}" || true)
			if [[ -z ${MATCHES} ]]; then
				continue
			fi

			while IFS=: read -r MATCH_LINE MATCH_CONTENT; do
				local ALLOWED=false

				# Check if match line is within any allowed range
				if [[ -n ${ALLOWED_RANGES} ]]; then
					# Convert pipe-delimited ranges to array
					IFS='|' read -ra RANGE_ARRAY <<<"${ALLOWED_RANGES}"
					for RANGE in "${RANGE_ARRAY[@]}"; do
						local RANGE_START
						local RANGE_END
						RANGE_START=$(echo "${RANGE}" | cut -d',' -f1)
						RANGE_END=$(echo "${RANGE}" | cut -d',' -f2)

						if [[ ${MATCH_LINE} -ge ${RANGE_START} ]] && [[ ${MATCH_LINE} -le ${RANGE_END} ]]; then
							ALLOWED=true
							break
						fi
					done
				fi

				if [[ ${ALLOWED} == false ]]; then
					echo "ERROR: Found forbidden pattern '${PATTERN}' in ${FILE} (line ${MATCH_LINE}, not in allowed fenced example)"
					echo "  ${MATCH_CONTENT}"
					echo ""
					ERROR_FOUND=1
				fi
			done <<<"${MATCHES}"
		done
	done
}

# selftest creates a temporary markdown file with allowed and forbidden legacy examples, runs check_doc_files to verify that forbidden patterns outside allowed fenced blocks are detected, and returns 0 on success or 1 on failure.
# It preserves and restores PATTERNS, DOC_FILES, and ERROR_FOUND around the test and removes the temporary file before returning.
selftest() {
	local TEST_FILE
	TEST_FILE=$(mktemp --suffix=_check_container_paths_test.md)

	# Write test file line by line to preserve backticks
	{
		echo "Test file for marker detection."
		echo ""
		echo ""
		echo "<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->"
		echo ""
		echo '```bash'
		echo "# This is allowed - legacy pattern in fenced block"
		echo "mmrelay --base-dir /opt/mmrelay"
		echo '```'
		echo ""
		echo "This should fail - legacy pattern outside fenced block:"
		echo "mmrelay --base-dir /bad/path"
		echo ""
		echo "<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->"
		echo ""
		echo ""
		echo '```bash'
		echo "# This is also allowed - multiple allowed blocks work"
		echo "export MMRELAY_BASE_DIR=/opt/mmrelay"
		echo '```'
		echo ""
		echo "<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->"
		echo ""
		echo ""
		echo '```bash'
		echo "# Allowed with backtick fence"
		echo "mmrelay --logfile /var/log/mmrelay.log"
		echo '```'
		echo ""
		echo "Another forbidden pattern outside allowed block:"
		echo "export MMRELAY_CREDENTIALS_PATH=/bad/path"
	} >"${TEST_FILE}"

	echo "Running self-test..."
	local OLD_PATTERNS=("${PATTERNS[@]}")
	local OLD_DOC_FILES=("${DOC_FILES[@]}")
	local OLD_ERROR_FOUND=${ERROR_FOUND}

	PATTERNS=("--base-dir" "--logfile" "MMRELAY_BASE_DIR" "MMRELAY_CREDENTIALS_PATH")
	DOC_FILES=("${TEST_FILE}")
	ERROR_FOUND=0

	# Capture output to verify error count
	local TEST_OUTPUT_FILE
	TEST_OUTPUT_FILE=$(mktemp)
	check_doc_files >"${TEST_OUTPUT_FILE}" 2>&1 || true

	# We expect 2 errors:
	# 1. --base-dir on line 12
	# 2. MMRELAY_CREDENTIALS_PATH on line 31
	local ERROR_COUNT
	ERROR_COUNT=$(grep -c "ERROR: Found forbidden pattern" "${TEST_OUTPUT_FILE}" || true)

	if [[ ${ERROR_FOUND} -eq 1 ]] && [[ ${ERROR_COUNT} -eq 2 ]]; then
		echo "✓ Self-test PASSED: Correctly detected forbidden patterns outside allowed blocks (Count: ${ERROR_COUNT})"
		rm -f "${TEST_FILE}"
		PATTERNS=("${OLD_PATTERNS[@]}")
		DOC_FILES=("${OLD_DOC_FILES[@]}")
		ERROR_FOUND=${OLD_ERROR_FOUND}
		return 0
	else
		echo "✗ Self-test FAILED: Expected 2 errors, found ${ERROR_COUNT}"
		cat "${TEST_OUTPUT_FILE}"
		rm -f "${TEST_FILE}" "${TEST_OUTPUT_FILE}"
		PATTERNS=("${OLD_PATTERNS[@]}")
		DOC_FILES=("${OLD_DOC_FILES[@]}")
		ERROR_FOUND=${OLD_ERROR_FOUND}
		return 1
	fi
}

if [[ ${CHECK_CONTAINER_PATHS_SELFTEST-} == "1" ]]; then
	selftest
	exit $?
fi

check_strict_files
check_doc_files

if [[ ${ERROR_FOUND} -eq 1 ]]; then
	echo ""
	echo "FAIL: Found legacy patterns that should not be in container/K8s/docs surfaces."
	echo ""
	echo "The following patterns are forbidden:"
	for PATTERN in "${PATTERNS[@]}"; do
		echo "  - ${PATTERN}"
	done
	echo ""
	echo "Strict files (forbidden anywhere):"
	for FILE in "${STRICT_FILES[@]}"; do
		echo "  - ${FILE}"
	done
	echo ""
	echo "Documentation files (allowed only with '<!-- MMRELAY_ALLOW_LEGACY_EXAMPLE -->' in fenced blocks):"
	for FILE in "${DOC_FILES[@]}"; do
		echo "  - ${FILE}"
	done
	echo ""
	echo "MMRELAY_HOME=/data is single source of truth for container deployments."
	exit 1
fi

echo "PASS: No legacy patterns found in checked files."
exit 0
