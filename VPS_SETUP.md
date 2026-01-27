# VPS Setup Guide - Polymarket Arbitrage Bot

This guide covers setting up and running the Polymarket Arbitrage Bot on a VPS (Virtual Private Server).

## Table of Contents
1. [VPS Requirements](#vps-requirements)
2. [Recommended VPS Providers](#recommended-vps-providers)
3. [Initial Server Setup](#initial-server-setup)
4. [Installing Dependencies](#installing-dependencies)
5. [Deploying the Bot](#deploying-the-bot)
6. [Running the Bot](#running-the-bot)
7. [Process Management with systemd](#process-management-with-systemd)
8. [Monitoring & Logs](#monitoring--logs)
9. [Security Best Practices](#security-best-practices)
10. [Troubleshooting](#troubleshooting)

---

## VPS Requirements

**Minimum Specifications:**
- **OS:** Ubuntu 22.04 LTS (recommended) or Debian 12
- **CPU:** 1 vCPU
- **RAM:** 1 GB
- **Storage:** 20 GB SSD
- **Network:** Stable internet connection

**Recommended Specifications:**
- **CPU:** 2 vCPU
- **RAM:** 2 GB
- **Storage:** 40 GB SSD

---

## Recommended VPS Providers

| Provider | Starting Price | Notes |
|----------|---------------|-------|
| DigitalOcean | $4/month | Easy setup, good documentation |
| Vultr | $5/month | Global locations, hourly billing |
| Linode | $5/month | Great performance |
| Hetzner | €3.79/month | Best value in EU |
| AWS Lightsail | $3.50/month | AWS ecosystem |

---

## Initial Server Setup

### 1. Connect to Your VPS

```bash
ssh root@YOUR_SERVER_IP
```

### 2. Update System Packages

```bash
apt update && apt upgrade -y
```

### 3. Create a Non-Root User (Recommended)

```bash
# Create user
adduser botuser

# Add to sudo group
usermod -aG sudo botuser

# Switch to new user
su - botuser
```

### 4. Configure Firewall

```bash
# Allow SSH
sudo ufw allow OpenSSH

# Enable firewall
sudo ufw enable

# Check status
sudo ufw status
```

---

## Installing Dependencies

### 1. Install Python 3.11+

```bash
# Install Python and pip
sudo apt install -y python3 python3-pip python3-venv

# Verify installation
python3 --version
```

### 2. Install Git

```bash
sudo apt install -y git
```

### 3. Install Additional Tools

```bash
sudo apt install -y curl wget htop
```

---

## Deploying the Bot

### 1. Clone the Repository

```bash
# Navigate to home directory
cd ~

# Clone the repo
git clone https://github.com/junjieooi/Poly_arbitragebot.git

# Enter the directory
cd Poly_arbitragebot
```

### 2. Create Virtual Environment

```bash
# Create venv
python3 -m venv venv

# Activate venv
source venv/bin/activate
```

### 3. Install Python Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables

```bash
# Copy the example file
cp .env.example .env

# Edit with your credentials
nano .env
```

**Required settings in `.env`:**

```env
# Polymarket API Credentials
POLYMARKET_API_KEY=your_api_key
POLYMARKET_API_SECRET=your_api_secret
POLYMARKET_PASSPHRASE=your_passphrase
POLYMARKET_PRIVATE_KEY=your_private_key

# Telegram Alerts
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_ENABLED=true

# Trading Mode (start with paper trading!)
PAPER_TRADING=true
```

**Save and exit:** Press `Ctrl+X`, then `Y`, then `Enter`

---

## Running the Bot

### Option 1: Quick Test Run

```bash
# Activate venv if not already
source venv/bin/activate

# Run the bot
python main.py
```

Press `Ctrl+C` to stop.

### Option 2: Run in Background with screen

```bash
# Install screen
sudo apt install -y screen

# Create a new screen session
screen -S polybot

# Activate venv and run
source venv/bin/activate
python main.py

# Detach from screen: Press Ctrl+A, then D
```

**Screen Commands:**
- `screen -ls` - List all sessions
- `screen -r polybot` - Reattach to session
- `screen -X -S polybot quit` - Kill session

### Option 3: Run in Background with tmux

```bash
# Install tmux
sudo apt install -y tmux

# Create new session
tmux new -s polybot

# Run the bot
source venv/bin/activate
python main.py

# Detach: Press Ctrl+B, then D
```

**tmux Commands:**
- `tmux ls` - List sessions
- `tmux attach -t polybot` - Reattach
- `tmux kill-session -t polybot` - Kill session

---

## Process Management with systemd

For production use, systemd ensures your bot automatically restarts on crashes and server reboots.

### 1. Create Service File

```bash
sudo nano /etc/systemd/system/polybot.service
```

### 2. Add Service Configuration

```ini
[Unit]
Description=Polymarket Arbitrage Bot
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/Poly_arbitragebot
Environment="PATH=/home/botuser/Poly_arbitragebot/venv/bin"
ExecStart=/home/botuser/Poly_arbitragebot/venv/bin/python main.py
Restart=always
RestartSec=10

# Logging
StandardOutput=append:/home/botuser/Poly_arbitragebot/logs/bot.log
StandardError=append:/home/botuser/Poly_arbitragebot/logs/error.log

[Install]
WantedBy=multi-user.target
```

### 3. Create Logs Directory

```bash
mkdir -p ~/Poly_arbitragebot/logs
```

### 4. Enable and Start the Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable polybot

# Start the service
sudo systemctl start polybot

# Check status
sudo systemctl status polybot
```

### 5. Service Management Commands

```bash
# Start
sudo systemctl start polybot

# Stop
sudo systemctl stop polybot

# Restart
sudo systemctl restart polybot

# View status
sudo systemctl status polybot

# View logs
sudo journalctl -u polybot -f
```

---

## Monitoring & Logs

### View Real-Time Logs

```bash
# Using journalctl (if using systemd)
sudo journalctl -u polybot -f

# Or tail the log file
tail -f ~/Poly_arbitragebot/logs/bot.log

# View trading.log
tail -f ~/Poly_arbitragebot/trading.log
```

### Monitor System Resources

```bash
# Install htop for better monitoring
sudo apt install -y htop

# Run htop
htop
```

### Set Up Log Rotation

```bash
sudo nano /etc/logrotate.d/polybot
```

Add:

```
/home/botuser/Poly_arbitragebot/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 botuser botuser
}
```

---

## Security Best Practices

### 1. Secure SSH Access

```bash
# Edit SSH config
sudo nano /etc/ssh/sshd_config
```

Recommended changes:
```
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
```

Restart SSH:
```bash
sudo systemctl restart sshd
```

### 2. Set Up SSH Key Authentication

On your **local machine**:
```bash
# Generate SSH key (if you don't have one)
ssh-keygen -t ed25519 -C "your_email@example.com"

# Copy key to server
ssh-copy-id botuser@YOUR_SERVER_IP
```

### 3. Protect Your .env File

```bash
# Set strict permissions
chmod 600 ~/.env
chmod 600 ~/Poly_arbitragebot/.env
```

### 4. Enable Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### 5. Install Fail2ban (Optional)

```bash
sudo apt install -y fail2ban
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

---

## Troubleshooting

### Bot Won't Start

1. **Check Python version:**
   ```bash
   python3 --version  # Should be 3.10+
   ```

2. **Verify venv is activated:**
   ```bash
   which python  # Should show venv path
   ```

3. **Check dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Verify .env file:**
   ```bash
   cat .env  # Check for typos
   ```

### Connection Errors

1. **Check internet:**
   ```bash
   ping google.com
   ```

2. **Test Polymarket API:**
   ```bash
   curl https://clob.polymarket.com/markets
   ```

3. **Check firewall:**
   ```bash
   sudo ufw status
   ```

### Telegram Alerts Not Working

1. **Verify bot token:**
   ```bash
   curl "https://api.telegram.org/bot<YOUR_TOKEN>/getMe"
   ```

2. **Check chat ID:**
   ```bash
   python fetch_telegram_id.py
   ```

### High Memory Usage

1. **Check memory:**
   ```bash
   free -h
   ```

2. **Add swap space:**
   ```bash
   sudo fallocate -l 1G /swapfile
   sudo chmod 600 /swapfile
   sudo mkswap /swapfile
   sudo swapon /swapfile
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

### View Service Errors

```bash
# Recent logs
sudo journalctl -u polybot -n 100

# Logs since boot
sudo journalctl -u polybot -b

# Logs with timestamps
sudo journalctl -u polybot --since "1 hour ago"
```

---

## Quick Start Checklist

- [ ] VPS provisioned with Ubuntu 22.04
- [ ] SSH access configured
- [ ] Python 3.10+ installed
- [ ] Repository cloned
- [ ] Virtual environment created
- [ ] Dependencies installed
- [ ] `.env` file configured
- [ ] Test run successful (paper trading)
- [ ] systemd service configured
- [ ] Telegram alerts working
- [ ] Logs accessible

---

## Updating the Bot

```bash
# Navigate to bot directory
cd ~/Poly_arbitragebot

# Stop the service
sudo systemctl stop polybot

# Pull latest changes
git pull origin main

# Activate venv
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt

# Restart the service
sudo systemctl start polybot
```

---

## Support

- **GitHub Issues:** https://github.com/junjieooi/Poly_arbitragebot/issues
- **Telegram Alerts:** Configured in `.env`

**Remember:** Always start with `PAPER_TRADING=true` to test your setup before using real funds!
