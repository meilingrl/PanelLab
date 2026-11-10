FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server python3 procps && rm -rf /var/lib/apt/lists/*
RUN rm -f /etc/ssh/ssh_host_*
RUN useradd -m -s /bin/bash lab && echo 'lab:panellab-fixture-only' | chpasswd && mkdir -p /run/sshd
RUN printf '\nPasswordAuthentication yes\nPermitRootLogin no\nUsePAM no\n' >> /etc/ssh/sshd_config
EXPOSE 22
CMD ["/bin/sh", "-c", "ssh-keygen -A && exec /usr/sbin/sshd -D -e"]
