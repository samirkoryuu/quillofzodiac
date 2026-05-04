# 🕸️ HiveWorks: The Ghost Network

Welcome to **HiveWorks**, the ultimate decentralized scraping and automation ecosystem. This workspace coordinates a fleet of "Ghost Nodes" (mobile phones) to perform high-priority scraping tasks without ever getting blocked.

## 📂 Project Structure

### 🤖 [Bot](./bot)
The brains of the operation. This is the Discord bot that manages users, rewards, and triggers the scraping tasks.
- **`main.py`**: Bot entry point.
- **`cogs/`**: Modular features (Admin, Verification, Economy, etc.).
- **`writers.db`**: Local database for staff and story tracking.

### 📡 [Hive Hub](./hive_hub) (Render)
The central command center. Hosted on Render, it bridges the Bot and the Ghost Nodes.
- **`main.py`**: FastAPI server that distributes tasks and serves OTA (Over-The-Air) updates.
- **`/latest-client`**: The endpoint where phones download their new "brains".

### 📱 [Mobile App](./mobile_app)
The "Ghost Node" shell. This is the stealth Android app installed on staff phones.
- **`systeminternet`**: The stealth identity of the app.
- **`loader.py`**: The boot-loader that pulls the latest scraping logic from the Hub.
- **`pyproject.toml`**: BeeWare configuration for building the `systeminternet.apk`.

---

## 🚀 How it works
1. **Instruction**: You push new scraping logic to the Hub.
2. **Update**: All connected phones automatically download the new logic via the OTA endpoint.
3. **Execution**: When the Bot needs data, the Hub picks a phone -> Phone scrapes -> Data returns to Bot.
4. **Result**: 100% human-looking traffic from real mobile IPs.

---
**Status**: 🟢 Network Active | 🛡️ Stealth: Maximum
