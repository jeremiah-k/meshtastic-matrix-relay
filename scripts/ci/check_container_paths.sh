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
		local TOTAL_LINES
		TOTAL_LINES=$(wc -l <"${FILE}")
		local LINE_NUM=1

		while [[ ${LINE_NUM} -le ${TOTAL_LINES} ]]; do
			local LINE_CONTENT
			LINE_CONTENT=$(sed -n "${LINE_NUM}p" "${FILE}")

			# Check if this line is the marker
			if echo "${LINE_CONTENT}" | grep -qF "${ALLOW_MARKER}"; then
				# Look for the next fence opening (allowing blank lines between marker and fence)
				local FENCE_LINE=$((LINE_NUM + 1))
				while [[ ${FENCE_LINE} -le ${TOTAL_LINES} ]]; do
					local FENCE_CONTENT
					FENCE_CONTENT=$(sed -n "${FENCE_LINE}p" "${FILE}")

					# Check for fence opening (``` or ~~~)
					if echo "${FENCE_CONTENT}" | grep -qE '^[[:space:]]*(`{3}|~{3})'; then
						# Found opening fence - capture the delimiter (``` or ~~~)
						local DELIMITER
						DELIMITER=$(echo "${FENCE_CONTENT}" | sed -nE 's/^[[:space:]]*(`{3}|~{3}).*/\1/p')

						# Find matching closing fence
						local CLOSING_LINE=$((FENCE_LINE + 1))
						while [[ ${CLOSING_LINE} -le ${TOTAL_LINES} ]]; do
							local CLOSING_CONTENT
							CLOSING_CONTENT=$(sed -n "${CLOSING_LINE}p" "${FILE}")

							if echo "${CLOSING_CONTENT}" | grep -qE "^[[:space:]]*${DELIMITER}[[:space:]]*$"; then
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

							CLOSING_LINE=$((CLOSING_LINE + 1))
						done
						break
					fi

					# Stop searching if we hit another marker or non-blank content
					# Only skip blank lines after marker
					if [[ ${FENCE_LINE} -gt $((LINE_NUM + 10)) ]]; then
						# Safety limit: don't search more than 10 lines after marker
						break
					fi

					local FENCE_TRIMMED
					FENCE_TRIMMED=$(echo "${FENCE_CONTENT}" | tr -d '[:space:]')
					if [[ -n ${FENCE_TRIMMED} ]] && ! grep -qE '^[[:space:]]*(```|~~~)' <<<"${FENCE_CONTENT}"; then
						# Found non-fence content - this marker has no following fence
						break
					fi

					FENCE_LINE=$((FENCE_LINE + 1))
				done
			fi

			LINE_NUM=$((LINE_NUM + 1))
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
	local TEST_FILE="/tmp/check_container_paths_test.md"

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

	check_doc_files

	if [[ ${ERROR_FOUND} -eq 1 ]]; then
		echo "✓ Self-test PASSED: Correctly detected forbidden patterns outside allowed blocks"
		rm -f "${TEST_FILE}"
		PATTERNS=("${OLD_PATTERNS[@]}")
		DOC_FILES=("${OLD_DOC_FILES[@]}")
		ERROR_FOUND=${OLD_ERROR_FOUND}
		return 0
	else
		echo "✗ Self-test FAILED: Should have detected forbidden patterns"
		rm -f "${TEST_FILE}"
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
