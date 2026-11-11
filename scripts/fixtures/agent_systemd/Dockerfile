# Debian with systemd as PID 1, for verifying the PanelLab Agent install.
# Build: docker build -t panellab-agent-lab:20261110 scripts/fixtures/agent_systemd
# Run:   see scripts/verify_agent_lab.py (needs --privileged --cgroupns=host and the cgroup mount)
#
# FIXTURE ONLY: the admin password and admin's NOPASSWD sudo exist so the verify
# script can run `sudo -n` non-interactively. Real servers ask admin for a password.
FROM debian:bookworm-slim
ENV container=docker
RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd systemd-sysv openssh-server python3 sudo procps \
 && rm -rf /var/lib/apt/lists/*
# Units that make no sense in a container and only produce noise or "degraded".
RUN systemctl mask getty@tty1.service serial-getty@console.service console-getty.service \
        systemd-udevd.service systemd-udevd-control.socket systemd-udevd-kernel.socket \
        systemd-modules-load.service sys-kernel-config.mount sys-kernel-debug.mount \
        sys-kernel-tracing.mount systemd-remount-fs.service systemd-firstboot.service \
        systemd-logind.service \
 && rm -f /etc/systemd/system/multi-user.target.wants/systemd-logind.service
RUN useradd -m -s /bin/bash admin \
 && echo 'admin:panellab-fixture-only' | chpasswd \
 && usermod -a -G sudo admin \
 && printf 'admin ALL=(ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/90-fixture-admin \
 && chmod 0440 /etc/sudoers.d/90-fixture-admin
# An unrelated existing account named "agent": the installer must leave it alone.
RUN useradd -m -s /bin/bash -c 'existing agent account' agent
COPY mail_sink.py /usr/local/lib/panellab-lab/mail_sink.py
COPY mail-sink.service /etc/systemd/system/mail-sink.service
RUN mkdir -p /var/mail-sink && systemctl enable mail-sink.service ssh.service
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
