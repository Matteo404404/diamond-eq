#!/bin/sh
# Installs the `diamondeq` command and the background service for this user.
# Needs: python3 with numpy and scipy, PipeWire with WirePlumber, pactl.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"

python3 -c "import numpy, scipy" || { echo "install python-numpy and python-scipy first" >&2; exit 1; }

mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
cat > "$HOME/.local/bin/diamondeq" <<EOF
#!/bin/sh
exec env PYTHONPATH="$DIR" python3 -m diamondeq.cli "\$@"
EOF
chmod +x "$HOME/.local/bin/diamondeq"

cat > "$HOME/.config/systemd/user/diamondeq.service" <<EOF
[Unit]
Description=Diamond EQ headphone equalizer
After=pipewire.service wireplumber.service pipewire-pulse.service

[Service]
ExecStart=$HOME/.local/bin/diamondeq serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable diamondeq.service
systemctl --user restart diamondeq.service
echo "Diamond EQ is running: http://127.0.0.1:8377"
