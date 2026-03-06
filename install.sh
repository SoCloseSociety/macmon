#!/bin/bash
# macmon installer — Mac Developer Monitor + System Cleaner
set -e

MACMON_DIR="$HOME/.macmon"
VENV_DIR="$MACMON_DIR/venv"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  __  __    _    ____ __  __  ___  _   _ "
echo " |  \/  |  / \  / ___|  \/  |/ _ \| \ | |"
echo " | |\/| | / _ \| |   | |\/| | | | |  \| |"
echo " | |  | |/ ___ \ |___| |  | | |_| | |\  |"
echo " |_|  |_/_/   \_\____|_|  |_|\___/|_| \_|"
echo ""
echo " Mac Developer Monitor + System Cleaner"
echo -e "${NC}"

# Check Python version
echo -e "${CYAN}Checking Python...${NC}"
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 9 ]); then
        echo -e "${RED}Python 3.9+ required (found $PY_VERSION)${NC}"
        echo "Install via: brew install python@3.12"
        exit 1
    fi
    echo -e "${GREEN}Python $PY_VERSION found${NC}"
else
    echo -e "${RED}Python 3 not found!${NC}"
    echo "Install via: brew install python@3.12"
    exit 1
fi

# Create macmon directory
echo -e "${CYAN}Setting up ~/.macmon...${NC}"
mkdir -p "$MACMON_DIR"
mkdir -p "$MACMON_DIR/reports"

# Create virtual environment
echo -e "${CYAN}Creating virtual environment...${NC}"
if [ -d "$VENV_DIR" ]; then
    echo -e "${YELLOW}Existing venv found, recreating...${NC}"
    rm -rf "$VENV_DIR"
fi
python3 -m venv "$VENV_DIR"

# Install dependencies
echo -e "${CYAN}Installing dependencies...${NC}"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt" -q

# Create wrapper script
echo -e "${CYAN}Creating macmon command...${NC}"
WRAPPER="/usr/local/bin/macmon"

# Check if we can write to /usr/local/bin
if [ -w "/usr/local/bin" ] || [ -w "$WRAPPER" ]; then
    cat > "$WRAPPER" << WRAPPER_EOF
#!/bin/bash
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/macmon.py" "\$@"
WRAPPER_EOF
    chmod +x "$WRAPPER"
    echo -e "${GREEN}Installed to $WRAPPER${NC}"
else
    echo -e "${YELLOW}Need sudo to install to /usr/local/bin${NC}"
    sudo bash -c "cat > '$WRAPPER'" << WRAPPER_EOF
#!/bin/bash
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/macmon.py" "\$@"
WRAPPER_EOF
    sudo chmod +x "$WRAPPER"
    echo -e "${GREEN}Installed to $WRAPPER${NC}"
fi

# Initialize config
echo -e "${CYAN}Initializing config...${NC}"
"$VENV_DIR/bin/python" "$SCRIPT_DIR/macmon.py" config --init 2>/dev/null || true

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} macmon installed successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${CYAN}Command Cheatsheet:${NC}"
echo ""
echo -e "  ${GREEN}macmon${NC}                        Live dashboard"
echo -e "  ${GREEN}macmon dashboard${NC}              Dashboard with custom refresh"
echo ""
echo -e "  ${GREEN}macmon ps${NC}                     List dev processes"
echo -e "  ${GREEN}macmon ps --sort ram${NC}          Sort by RAM usage"
echo -e "  ${GREEN}macmon ps --filter llm${NC}        Filter by category"
echo -e "  ${GREEN}macmon ps --tree${NC}              Process hierarchy"
echo -e "  ${GREEN}macmon kill <name|pid>${NC}        Kill a process"
echo -e "  ${GREEN}macmon suspend <name|pid>${NC}     Suspend a process"
echo -e "  ${GREEN}macmon resume <name|pid>${NC}      Resume a process"
echo ""
echo -e "  ${GREEN}macmon sweep${NC}                  Kill zombies + orphans + stale locks"
echo -e "  ${GREEN}macmon ports${NC}                  Show port usage"
echo -e "  ${GREEN}macmon ports --free 3000${NC}      Free a port"
echo -e "  ${GREEN}macmon ports --free-all-dev${NC}   Free all dev ports"
echo -e "  ${GREEN}macmon purge${NC}                  Purge inactive RAM"
echo ""
echo -e "  ${GREEN}macmon clean --scan${NC}           Preview system cleanup"
echo -e "  ${GREEN}macmon clean --run${NC}            Interactive cleanup"
echo -e "  ${GREEN}macmon clean --all -y${NC}         Full auto cleanup"
echo -e "  ${GREEN}macmon clean --browsers${NC}       Browser cache cleanup"
echo -e "  ${GREEN}macmon clean --module xcode${NC}   Clean specific app"
echo -e "  ${GREEN}macmon clean --clipboard${NC}      Clear clipboard"
echo ""
echo -e "  ${GREEN}macmon gc --scan${NC}              Preview dev garbage"
echo -e "  ${GREEN}macmon gc --clean${NC}             Interactive dev cleanup"
echo -e "  ${GREEN}macmon gc --all -y${NC}            Full dev garbage nuke"
echo ""
echo -e "  ${GREEN}macmon privacy --scan${NC}         List privacy traces"
echo -e "  ${GREEN}macmon privacy --clean${NC}        Interactive privacy wipe"
echo -e "  ${GREEN}macmon privacy --full -y${NC}      Full trace wipe"
echo ""
echo -e "  ${GREEN}macmon health${NC}                 System health check (/100)"
echo -e "  ${GREEN}macmon health --fix${NC}           Auto-fix safe issues"
echo -e "  ${GREEN}macmon health --report${NC}        Save health report"
echo ""
echo -e "  ${GREEN}macmon startup --list${NC}         List startup items"
echo -e "  ${GREEN}macmon startup --broken${NC}       Show broken items"
echo -e "  ${GREEN}macmon startup --audit${NC}        Flag suspicious items"
echo -e "  ${GREEN}macmon startup --disable <n>${NC}  Disable startup item"
echo ""
echo -e "  ${GREEN}macmon uninstall <App>${NC}        Full app uninstaller"
echo -e "  ${GREEN}macmon uninstall --list${NC}       List all apps by size"
echo -e "  ${GREEN}macmon uninstall --scan <App>${NC} Preview leftovers"
echo ""
echo -e "  ${GREEN}macmon dupes ~/Downloads${NC}      Find duplicate files"
echo -e "  ${GREEN}macmon dupes --empty-dirs${NC}     Find empty directories"
echo -e "  ${GREEN}macmon dupes --broken-symlinks${NC} Find broken symlinks"
echo ""
echo -e "  ${GREEN}macmon bigfiles${NC}               Find large files"
echo -e "  ${GREEN}macmon bigfiles --min 100MB${NC}   Custom size threshold"
echo -e "  ${GREEN}macmon bigfiles --older 90${NC}    Files not accessed 90+ days"
echo ""
echo -e "  ${GREEN}macmon disk${NC}                   Disk usage analyzer"
echo -e "  ${GREEN}macmon disk ~/Projects${NC}        Analyze specific directory"
echo ""
echo -e "  ${GREEN}macmon network${NC}                Network connections"
echo -e "  ${GREEN}macmon network --listening${NC}    Listening ports only"
echo -e "  ${GREEN}macmon flush-dns${NC}              Flush DNS cache"
echo ""
echo -e "  ${GREEN}macmon security${NC}               Full security audit (/100)"
echo -e "  ${GREEN}macmon security --connections${NC}  Scan active connections"
echo -e "  ${GREEN}macmon security --firewall${NC}    Firewall status"
echo -e "  ${GREEN}macmon security --malware${NC}     Malware indicator scan"
echo -e "  ${GREEN}macmon security --remote${NC}      Remote access tool detection"
echo -e "  ${GREEN}macmon security --rules${NC}       Active security rules"
echo -e "  ${GREEN}macmon security --block-ip X${NC}  Block an IP address"
echo -e "  ${GREEN}macmon security --quarantine X${NC} Kill + block a process"
echo ""
echo -e "  ${GREEN}macmon docker${NC}                 Docker overview"
echo -e "  ${GREEN}macmon docker --containers${NC}    List all containers"
echo -e "  ${GREEN}macmon docker --images${NC}        List images"
echo -e "  ${GREEN}macmon docker --volumes${NC}       List volumes"
echo -e "  ${GREEN}macmon docker --prune -y${NC}      Full Docker cleanup"
echo -e "  ${GREEN}macmon docker --stop-all${NC}      Stop all containers"
echo -e "  ${GREEN}macmon docker --stats${NC}         Live container stats"
echo -e "  ${GREEN}macmon docker --compose${NC}       List Compose projects"
echo -e "  ${GREEN}macmon docker --scan${NC}          Docker security audit"
echo ""
echo -e "  ${GREEN}macmon auto --start${NC}           Start autopilot daemon"
echo -e "  ${GREEN}macmon auto --stop${NC}            Stop autopilot daemon"
echo -e "  ${GREEN}macmon auto --status${NC}          Daemon status + recent actions"
echo ""
echo -e "  ${GREEN}macmon focus${NC}                  Enter focus mode"
echo -e "  ${GREEN}macmon restore${NC}                Restore after focus"
echo ""
echo -e "  ${GREEN}macmon report${NC}                 Session summary"
echo -e "  ${GREEN}macmon report --full${NC}          Full report with health check"
echo -e "  ${GREEN}macmon config --show${NC}          View config"
echo -e "  ${GREEN}macmon config --edit${NC}          Edit config in \$EDITOR"
echo ""
echo -e "${CYAN}Config: ~/.macmon/config.toml${NC}"
echo -e "${CYAN}Logs:   ~/.macmon/macmon.log${NC}"
echo -e "${CYAN}DB:     ~/.macmon/macmon.db${NC}"
echo ""
echo -e "${GREEN}Ready! Run 'macmon' to launch the dashboard.${NC}"
