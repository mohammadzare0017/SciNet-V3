# SciNet

# 🧠 SciNet Bot (Fast Mode)

این پروژه یک **ربات خودکار پایتونی** است که وارد سایت SciNet می‌شود، درخواست‌های کاربران را رصد می‌کند،  
در صورت مشاهده‌ی درخواست جدید، آن را به **تلگرام** گزارش می‌دهد تا اپراتور بتواند مقاله را پردازش و تحویل دهد.

---

## 🚀 راه‌اندازی سریع

### ۱. کلون پروژه
```bash
git clone https://github.com/mohammadzare0017/SciNet
cd SciNet
```
# Deploy SciNet Bot on VPS (Docker)

این راهنما فرض می‌کند شما به VPS با دسترسی sudo دسترسی دارید و Docker + docker-compose نصب است.

## 1. نصب Docker (در اوبونتو)
```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```
