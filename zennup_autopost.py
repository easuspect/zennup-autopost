name: Gunluk Zennup Paylasimi

on:
  schedule:
    # UTC 18:00 = Kaliforniya saatiyle 10:00 (kis) / 11:00 (yaz)
    # Saati degistirmek icin: https://crontab.guru
    - cron: "0 18 * * *"
  workflow_dispatch: {}   # Actions sekmesinden elle de calistirabilirsiniz

permissions:
  contents: write

jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - name: Repoyu indir
        uses: actions/checkout@v4

      - name: Python kur
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Bagimliliklari yukle
        run: pip install -r requirements.txt

      - name: Paylasimi yap
        env:
          META_ACCESS_TOKEN: ${{ secrets.META_ACCESS_TOKEN }}
          IG_USER_ID: ${{ secrets.IG_USER_ID }}
          FB_PAGE_ID: ${{ secrets.FB_PAGE_ID }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: python zennup_autopost.py

      - name: Paylasilan dosyayi posted klasorune commit'le
        run: |
          git config user.name "zennup-bot"
          git config user.email "bot@users.noreply.github.com"
          git add -A media/
          git diff --cached --quiet || git commit -m "Paylasildi: gunluk post"
          git push
