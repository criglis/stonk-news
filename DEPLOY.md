# Deploying Stonk News to the Web (Free)

Follow these steps to put your app online so you can use it from any phone or browser.

---

## Step 1 — Create a free GitHub account

1. Go to **https://github.com** and click **Sign up**
2. Choose a username, enter your email, create a password, and verify your account

---

## Step 2 — Create a new GitHub repository

1. Once logged in, click the **+** icon (top right) → **New repository**
2. Name it: `stonk-news`
3. Keep it **Public**
4. Click **Create repository**

---

## Step 3 — Upload the app files to GitHub

1. On your new repository page, click **uploading an existing file**
2. Drag and drop ALL the files from the `stonk-news-app` folder:
   - `app.py`
   - `requirements.txt`
   - `Procfile`
   - The `templates` folder (containing `index.html`)
3. Scroll down and click **Commit changes**

> **Note:** GitHub does not allow uploading folders directly from the browser.  
> For the `templates/index.html`, create the folder first:  
> Click **Create new file** → type `templates/index.html` in the name box → paste the contents of `index.html` → commit.

---

## Step 4 — Create a free Render account

1. Go to **https://render.com** and click **Get Started for Free**
2. Sign up using your **GitHub** account (click "Continue with GitHub") — this connects them automatically

---

## Step 5 — Deploy the app on Render

1. In your Render dashboard, click **New +** → **Web Service**
2. Select **Connect a repository** → choose `stonk-news`
3. Fill in the settings:
   | Field | Value |
   |---|---|
   | Name | stonk-news (or anything you like) |
   | Region | Choose the one closest to you |
   | Branch | main |
   | Runtime | **Python 3** |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `gunicorn app:app` |
4. Choose the **Free** plan
5. Click **Create Web Service**

Render will build and deploy your app — this takes about 2–3 minutes.

---

## Step 6 — Open your app on your phone

Once deployed, Render gives you a URL like:
```
https://stonk-news.onrender.com
```

Open that URL on your phone — and bookmark it for easy access!

---

## Notes

- **Free tier limitation:** Render's free plan "sleeps" after 15 minutes of no activity.
  The first visit after a sleep takes ~30 seconds to wake up. This is normal.
- **News coverage:** News is fetched from Google News and Yahoo Finance RSS feeds,
  which aggregate Reuters, CNBC, MarketWatch, Seeking Alpha, and others.
- **Stock data:** Provided by Yahoo Finance via the `yfinance` library.
- **Currency:** All prices are automatically converted to EUR using the exchange rate
  for each specific day.
