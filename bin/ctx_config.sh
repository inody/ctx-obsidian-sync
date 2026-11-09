# shellcheck shell=bash
# Load ctx-obsidian-sync settings into the environment. Sourced by the wrappers.
#
# Same rules as ctx_config.py: KEY=value lines, known keys only, the file is
# parsed rather than executed, and a variable already present in the
# environment (even if empty) wins over the file. Compatible with macOS
# /bin/bash 3.2 (no associative arrays).

CTX_CONFIG_KEYS=" OBSIDIAN_VAULT CTX_OBSIDIAN_TZ CTX_OBSIDIAN_DIARY_DIR CTX_OBSIDIAN_DIARY_TEMPLATE CTX_OBSIDIAN_LAUNCHD_PREFIX CTX_DAILY_DIGEST_HOST CTX_DAILY_DIGEST_MODEL CTX_DAILY_DIGEST_LANGUAGE CTX_DAILY_DIGEST_PROMPT_FILE CTX_DAILY_DIGEST_HEADING CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS "

ctx_config_path() {
  if [[ -n "${CTX_OBSIDIAN_SYNC_CONFIG:-}" ]]; then
    printf '%s\n' "${CTX_OBSIDIAN_SYNC_CONFIG/#\~/$HOME}"
  else
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/ctx-obsidian-sync/config"
  fi
}

ctx_config_load() {
  local file line key value number=0 from_file=" "
  file="$(ctx_config_path)"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    number=$((number + 1))
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    if [[ ! "$line" =~ ^(export[[:space:]]+)?([A-Z_][A-Z0-9_]*)=(.*)$ ]]; then
      echo "warn: $file:$number: ignoring malformed line" >&2
      continue
    fi
    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    if [[ "$CTX_CONFIG_KEYS" != *" $key "* ]]; then
      echo "warn: $file:$number: ignoring unknown key $key" >&2
      continue
    fi
    value="${value#"${value%%[![:space:]]*}"}"
    if [[ ${#value} -ge 2 && ( "$value" == \"*\" || "$value" == \'*\' ) ]]; then
      value="${value:1:${#value}-2}"
    else
      value="${value%%[[:space:]]#*}"
      value="${value%"${value##*[![:space:]]}"}"
    fi
    # shellcheck disable=SC2088  # literal "~" is matched on purpose
    if [[ "$value" == "~" || "$value" == "~/"* ]]; then
      value="$HOME${value:1}"
    fi
    # Environment wins; within the file the last assignment wins (as in Python).
    if [[ -n "${!key+x}" && "$from_file" != *" $key "* ]]; then
      continue
    fi
    export "$key=$value"
    from_file="$from_file$key "
  done < "$file"
}
