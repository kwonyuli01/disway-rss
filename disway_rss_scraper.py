#!/usr/bin/env python3
"""
Disway.id RSS Feed Scraper with Full Article Content
=====================================================
- Hanya artikel BARU yang masuk feed (belum pernah di-scrape sebelumnya)
- Tanggal artikel = waktu saat scraping (date NOW), bukan tanggal asli
- Artikel lama di-track via seen_articles.json
- Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import time
import re
import os
import html
import hashlib
import json

# ============================================================
# KONFIGURASI
# ============================================================

SCRAPE_URLS = [
    "https://disway.id/listtag/224235/saldo-dana-gratis",
]

MAX_ARTICLES = 20
FEED_TITLE = "Disway.id - Saldo Dana Gratis"
FEED_DESCRIPTION = "RSS Feed dari disway.id dengan konten artikel lengkap"
FEED_LINK = "https://disway.id"
OUTPUT_FILE = "docs/feed.xml"
SEEN_FILE = "seen_articles.json"  # Track artikel yang sudah pernah masuk
FEED_MAX_AGE_HOURS = 3  # Artikel dihapus dari feed setelah X jam
SEEN_MAX_AGE_DAYS = 30  # Hapus tracking artikel setelah X hari
REQUEST_DELAY = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
})


# ============================================================
# TRACKING ARTIKEL YANG SUDAH PERNAH MASUK
# ============================================================

def load_seen_articles():
    """Load daftar artikel yang sudah pernah di-scrape."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_seen_articles(seen):
    """Simpan daftar artikel yang sudah pernah di-scrape."""
    # Cleanup: hapus artikel yang lebih tua dari SEEN_MAX_AGE_DAYS
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_MAX_AGE_DAYS)).isoformat()
    cleaned = {url: data for url, data in seen.items() if data.get('first_seen', '') > cutoff}

    with open(SEEN_FILE, 'w', encoding='utf-8') as f:
        json.dump(cleaned, f, indent=2, ensure_ascii=False)

    print(f"  [i] Tracked articles: {len(cleaned)} (cleaned {len(seen) - len(cleaned)} old entries)")


# ============================================================
# FETCH & PARSE FUNCTIONS
# ============================================================

def fetch_page(url, retries=3):
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.RequestException as e:
            print(f"  [!] Gagal fetch {url} (percobaan {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * 2)
    return None


def parse_list_page(url):
    print(f"\n[*] Scraping halaman list: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    headings = soup.select('h2.media-heading a')
    if not headings:
        headings = soup.select('a[href*="/read/"]')

    for link in headings:
        href = link.get('href', '')
        title = link.get_text(strip=True)
        if not href or not title:
            continue
        if href.startswith('/'):
            href = 'https://disway.id' + href
        if '/read/' not in href and '/catatan-harian-dahlan/' not in href:
            continue
        if any(a['link'] == href for a in articles):
            continue
        articles.append({'title': title, 'link': href})
        if len(articles) >= MAX_ARTICLES:
            break

    print(f"  [+] Ditemukan {len(articles)} artikel di halaman")
    return articles


def parse_article_page(url):
    print(f"  [>] Mengambil artikel: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'lxml')
    article_data = {}

    # JUDUL
    h1 = soup.find('h1')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # TANGGAL ASLI (simpan sebagai referensi di konten, tapi TIDAK dipakai sebagai pubDate)
    date_text = ''
    for text in soup.find_all(string=re.compile(r'(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu)\s+\d{2}-\d{2}-\d{4}')):
        date_text = text.strip()
        break
    if not date_text:
        for text in soup.find_all(string=re.compile(r'\d{2}-\d{2}-\d{4},\s*\d{2}:\d{2}')):
            date_text = text.strip()
            break
    article_data['original_date'] = date_text

    # REPORTER & EDITOR
    reporter = ''
    editor = ''
    for tag in soup.find_all(['p', 'span', 'div']):
        text = tag.get_text()
        if 'Reporter:' in text or 'Penulis:' in text:
            bold = tag.find('b') or tag.find('strong')
            if bold:
                reporter = bold.get_text(strip=True)
            else:
                match = re.search(r'Reporter:\s*\**(.+?)(?:\||$)', text)
                if match:
                    reporter = match.group(1).strip().strip('*')
        if 'Editor:' in text:
            bold = tag.find('b') or tag.find('strong')
            if bold:
                editor = bold.get_text(strip=True)
            else:
                match = re.search(r'Editor:\s*\**(.+?)(?:\||$)', text)
                if match:
                    editor = match.group(1).strip().strip('*')
    article_data['reporter'] = reporter
    article_data['editor'] = editor

    # GAMBAR UTAMA
    main_image = ''
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'cms.disway.id/uploads/' in src and '/medium/' not in src and '/small/' not in src:
            main_image = src
            break
    if not main_image:
        for img in soup.find_all('img'):
            src = img.get('src', '')
            if 'cms.disway.id/uploads/' in src:
                main_image = src
                break
    article_data['image'] = main_image

    # CAPTION
    caption = ''
    if main_image:
        img_tag = soup.find('img', src=main_image)
        if img_tag:
            next_elem = img_tag.find_next(['p', 'div', 'figcaption', 'span'])
            if next_elem and len(next_elem.get_text(strip=True)) < 200:
                potential_caption = next_elem.get_text(strip=True)
                if not potential_caption.startswith(('JAKARTA', 'BANDUNG', 'SURABAYA', 'Dalam', 'Pada')):
                    caption = potential_caption
    article_data['caption'] = caption

    # KONTEN ARTIKEL
    content_parts = []
    found_content = False
    all_paragraphs = soup.find_all('p')

    for p in all_paragraphs:
        text = p.get_text(strip=True)
        if not text:
            continue
        parent_classes = ' '.join(p.parent.get('class', []))
        if any(skip in parent_classes for skip in ['sidebar', 'footer', 'nav', 'menu', 'comment']):
            continue
        if any(skip in text for skip in ['Reporter:', 'Editor:', 'Penulis:', 'Cek Berita dan Artikel',
                                          'Temukan Berita Terkini', 'Google News', 'WhatsApp Channel']):
            continue
        if len(text) < 20 and not text.startswith(('●', '•', '-', '1.', '2.', '3.', '4.', '5.')):
            continue
        if text == caption:
            continue
        if re.match(r'^[A-Z]{2,}.+DISWAY\.ID', text) or re.match(r'^\*\*[A-Z]', text):
            found_content = True
        if not found_content and len(text) > 50:
            found_content = True
        if found_content:
            clean_text = text.replace('\xa0', ' ').strip()
            if clean_text:
                content_parts.append(clean_text)

    article_content = extract_structured_content(soup, content_parts)
    article_data['content'] = article_content

    # MULTI-PAGE
    next_pages = []
    for a in soup.find_all('a'):
        href = a.get('href', '')
        if href and re.match(r'.+/read/.+/\d+$', href):
            page_url = href if href.startswith('http') else 'https://disway.id' + href
            if page_url not in next_pages and page_url != url:
                next_pages.append(page_url)

    for page_url in next_pages[:5]:
        print(f"    [>] Halaman lanjutan: {page_url}")
        time.sleep(REQUEST_DELAY)
        page_content = fetch_additional_page(page_url)
        if page_content:
            article_data['content'] += '\n\n' + page_content

    # TAG
    tags = []
    for tag_link in soup.select('a[href*="/listtag/"]'):
        tag_text = tag_link.get_text(strip=True).replace('#', '').strip()
        if tag_text:
            tags.append(tag_text)
    article_data['tags'] = tags

    # KATEGORI
    category = ''
    breadcrumb_links = soup.select('a[href*="/kategori/"]')
    for bl in breadcrumb_links:
        cat_text = bl.get_text(strip=True)
        if cat_text and cat_text not in ['Home', '']:
            category = cat_text
            break
    article_data['category'] = category

    return article_data


def extract_structured_content(soup, paragraph_texts):
    structured_parts = []
    for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p']):
        text = element.get_text(strip=True)
        if not text or len(text) < 5:
            continue
        if element.name == 'h1':
            continue
        parent_classes = ' '.join(element.parent.get('class', []) if element.parent else [])
        if any(skip in parent_classes for skip in ['sidebar', 'footer', 'nav', 'terkini', 'populer', 'pilihan']):
            continue
        if element.name in ['h2', 'h3', 'h4']:
            if text in ['Terkini', 'Terpopuler', 'Pilihan', 'Berita Terkait']:
                continue
            structured_parts.append(f"\n### {text}\n")
        elif text in paragraph_texts:
            structured_parts.append(text)

    if structured_parts:
        return '\n\n'.join(structured_parts)
    return '\n\n'.join(paragraph_texts)


def fetch_additional_page(url):
    html_content = fetch_page(url)
    if not html_content:
        return ''
    soup = BeautifulSoup(html_content, 'lxml')
    content_parts = []
    for p in soup.find_all('p'):
        text = p.get_text(strip=True)
        if not text or len(text) < 20:
            continue
        parent_classes = ' '.join(p.parent.get('class', []))
        if any(skip in parent_classes for skip in ['sidebar', 'footer', 'nav']):
            continue
        if any(skip in text for skip in ['Reporter:', 'Editor:', 'Cek Berita', 'Google News', 'WhatsApp Channel']):
            continue
        content_parts.append(text)
    return '\n\n'.join(content_parts)


# ============================================================
# RSS GENERATION
# ============================================================

def make_pub_date(dt=None):
    """Buat tanggal RFC 822 dari datetime object. Default = sekarang."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:00 +0700"


def generate_rss(articles_data):
    print(f"\n[*] Generating RSS XML with {len(articles_data)} new articles...")
    now = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')

    rss_items = []
    for article in articles_data:
        if not article:
            continue

        content_html = ''
        if article.get('image'):
            content_html += f'<p><img src="{html.escape(article["image"])}" alt="{html.escape(article.get("title", ""))}" style="max-width:100%;" /></p>\n'
        if article.get('caption'):
            content_html += f'<p><em>{html.escape(article["caption"])}</em></p>\n'
        if article.get('reporter'):
            content_html += f'<p><strong>Reporter:</strong> {html.escape(article["reporter"])}'
            if article.get('editor'):
                content_html += f' | <strong>Editor:</strong> {html.escape(article["editor"])}'
            content_html += '</p>\n'

        # Tampilkan tanggal asli di dalam konten sebagai referensi
        if article.get('original_date'):
            content_html += f'<p><em>Tanggal asli: {html.escape(article["original_date"])}</em></p>\n'

        if article.get('content'):
            paragraphs = article['content'].split('\n\n')
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if para.startswith('### '):
                    content_html += f'<h3>{html.escape(para[4:])}</h3>\n'
                else:
                    content_html += f'<p>{html.escape(para)}</p>\n'
        if article.get('tags'):
            tags_str = ', '.join(article['tags'])
            content_html += f'<p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n'

        guid = article.get('link', hashlib.md5(article.get('title', '').encode()).hexdigest())

        rss_items.append({
            'title': article.get('title', 'Tanpa Judul'),
            'link': article.get('link', ''),
            'description': content_html,
            'pubDate': article.get('pub_date', now),  # Ini sudah date NOW
            'category': article.get('category', ''),
            'tags': article.get('tags', []),
            'guid': guid,
            'image': article.get('image', ''),
        })

    rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{html.escape(FEED_TITLE)}</title>
    <description>{html.escape(FEED_DESCRIPTION)}</description>
    <link>{html.escape(FEED_LINK)}</link>
    <language>id</language>
    <lastBuildDate>{now}</lastBuildDate>
    <generator>Disway RSS Scraper (GitHub Actions)</generator>
'''

    for item in rss_items:
        rss_xml += f'''    <item>
      <title><![CDATA[{item['title']}]]></title>
      <link>{html.escape(item['link'])}</link>
      <guid isPermaLink="true">{html.escape(item['guid'])}</guid>
      <pubDate>{item['pubDate']}</pubDate>
'''
        if item['category']:
            rss_xml += f'      <category><![CDATA[{item["category"]}]]></category>\n'
        for tag in item.get('tags', []):
            rss_xml += f'      <category><![CDATA[{tag}]]></category>\n'
        if item['image']:
            rss_xml += f'      <media:content url="{html.escape(item["image"])}" medium="image" />\n'
        rss_xml += f'      <description><![CDATA[{item["description"]}]]></description>\n'
        rss_xml += f'      <content:encoded><![CDATA[{item["description"]}]]></content:encoded>\n'
        rss_xml += '    </item>\n'

    rss_xml += '''  </channel>
</rss>'''
    return rss_xml


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 60)
    print("  Disway.id RSS Scraper - NEW Articles Only")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    # Step 1: Load daftar artikel yang sudah pernah di-scrape
    seen = load_seen_articles()
    print(f"  [i] Artikel yang sudah ditrack: {len(seen)}")

    # Step 2: Scrape halaman list
    all_articles = []
    for url in SCRAPE_URLS:
        articles = parse_list_page(url)
        all_articles.extend(articles)
        time.sleep(REQUEST_DELAY)

    if not all_articles:
        print("\n[!] Tidak ada artikel ditemukan.")
        # Tetap generate feed kosong
        rss_xml = generate_rss([])
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
        return

    # Step 3: Filter hanya artikel BARU (belum pernah masuk seen)
    new_articles = []
    for article in all_articles:
        if article['link'] not in seen:
            new_articles.append(article)

    print(f"\n[*] Artikel baru: {len(new_articles)} dari {len(all_articles)} total")

    if not new_articles:
        print("[i] Tidak ada artikel baru. Feed tetap kosong.")
        # Generate feed kosong (atau pertahankan artikel recent)
        # Ambil artikel yang masih dalam window FEED_MAX_AGE_HOURS
        recent_in_feed = get_recent_feed_articles(seen)
        rss_xml = generate_rss(recent_in_feed)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(rss_xml)
        save_seen_articles(seen)
        return

    # Step 4: Fetch konten lengkap HANYA untuk artikel baru
    now = datetime.now(timezone.utc)
    articles_data = []

    for i, article in enumerate(new_articles):
        print(f"\n--- Artikel Baru {i+1}/{len(new_articles)} ---")
        article_data = parse_article_page(article['link'])

        # Set pubDate ke waktu SEKARANG (bukan tanggal asli)
        pub_date_now = make_pub_date(now + timedelta(minutes=i))  # Spread waktu 1 menit per artikel

        if article_data:
            if not article_data.get('title'):
                article_data['title'] = article['title']
            article_data['link'] = article['link']
            article_data['pub_date'] = pub_date_now
            articles_data.append(article_data)
        else:
            articles_data.append({
                'title': article['title'],
                'link': article['link'],
                'content': '(Konten tidak dapat diambil)',
                'pub_date': pub_date_now,
                'original_date': '',
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })

        # Tandai sebagai sudah pernah di-scrape
        seen[article['link']] = {
            'title': article['title'],
            'first_seen': now.isoformat(),
            'pub_date': pub_date_now,
        }

        time.sleep(REQUEST_DELAY)

    # Step 5: Tambahkan juga artikel recent yang masih dalam window
    recent_in_feed = get_recent_feed_articles(seen)
    # Gabungkan: artikel baru + artikel recent yang masih dalam window
    all_feed_articles = articles_data + recent_in_feed

    # Step 6: Generate & simpan RSS
    rss_xml = generate_rss(all_feed_articles)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(rss_xml)

    # Step 7: Simpan tracking
    save_seen_articles(seen)

    print(f"\n{'=' * 60}")
    print(f"  SELESAI!")
    print(f"  Artikel baru di feed  : {len(articles_data)}")
    print(f"  Artikel recent di feed: {len(recent_in_feed)}")
    print(f"  Total di feed         : {len(all_feed_articles)}")
    print(f"  File: {OUTPUT_FILE}")
    print(f"{'=' * 60}")


def get_recent_feed_articles(seen):
    """Ambil artikel dari seen yang masih dalam window FEED_MAX_AGE_HOURS.
    Return sebagai list minimal data (tanpa re-fetch konten)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=FEED_MAX_AGE_HOURS)).isoformat()
    recent = []
    for url, data in seen.items():
        if data.get('first_seen', '') > cutoff and data.get('pub_date'):
            recent.append({
                'title': data.get('title', 'Tanpa Judul'),
                'link': url,
                'content': '',  # Tidak re-fetch konten lama
                'pub_date': data.get('pub_date', ''),
                'original_date': '',
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })
    return recent


if __name__ == '__main__':
    main()
