# -*- coding: utf-8 -*-
import requests
import init
import time
from datetime import datetime
from sqlitelib import *
from bs4 import BeautifulSoup
from telegram import Bot
from message_queue import add_task_to_queue


def add_subscribe2db(actor_name, sub_user):
    headers = {
        "user-agent": init.USER_AGENT,
        "Cookie": "existmag=mag; PHPSESSID=8audoo3ikfst3khs32f35uu6l5; age_verified=1",
        # 'cache-control': 'max-age=0',
        "Priority": "u=1, i",
        
    }
    base_url = f"https://www.javbus.com/search/{actor_name}"
    response = requests.get(base_url, headers=headers)
    if response.status_code != 200:
        init.logger.error(f"获取女优信息失败: {response.status_code}")
        return False
    soup = BeautifulSoup(response.text, 'html.parser')
    max_page = get_max_page(soup)
    for page in range(1, max_page + 1):
        base_url = f"https://www.javbus.com/search/{actor_name}"
        if page > 1:
            base_url = f"https://www.javbus.com/search/{actor_name}/{page}"
        response = requests.get(base_url, headers=headers)
        if response.status_code != 200:
            continue
        items = soup.find_all('div', class_='item masonry-brick')

        for item in items:
            # Extract title
            title = item.find('img')['title']
            
            # Extract publication URL
            pub_url = item.find('a', class_='movie-box')['href']
            pub_url = pub_url.replace('thumb', 'cover')
            
            # Extract dates and number
            photo_info = item.find('div', class_='photo-info')
            dates = photo_info.find_all('date')
            number = dates[0].text.strip()
            date = dates[1].text.strip()
            buttons = photo_info.find_all('button')
            if not buttons:
                print(f"title: {title}")
                print(f"pub_url: {pub_url}")
                print(f"date: {date}")
                print(f"number: {number}")
                print("-" * 50)
         
                
def get_max_page(soup):
    try:
        # Try both possible selectors for pagination
        pagination = soup.find('ul', class_='pagination')
        if not pagination:
            pagination = soup.find('div', class_='text-center hidden-xs')
        
        if pagination:
            # Get all page number links (excluding previous/next buttons)
            page_links = [a for a in pagination.find_all('a') 
                         if not a.get('id') in ['pre', 'next']]
            
            if page_links:
                # Extract numbers from hrefs
                page_numbers = []
                for link in page_links:
                    try:
                        num = int(link['href'].split('/')[-1])
                        page_numbers.append(num)
                    except (ValueError, KeyError):
                        continue
                
                if page_numbers:
                    return max(page_numbers)
        
        # If we get here, no pagination found
        return 1
    
    except Exception as e:
        print(f"Error finding max page: {e}")
        return 1  # Default to single page if any error occurs
    
def age_verify():
    # URL of the website (replace with actual URL)
    url = "https://www.javbus.com"  

    # Create a session to maintain cookies
    session = requests.Session()

    # First, get the page to see if age verification is required
    response = session.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')

    # Check if age verification modal exists
    age_verify_modal = soup.find('div', id='ageVerify')
    if age_verify_modal and 'show' in age_verify_modal.get('class', []):
        print("Age verification required")
        
        # Prepare form data
        form_data = {
            'Submit': '確認',  # The submit button value
            'checkbox': True,
        }
        
        # Submit the form (note: some sites may require additional hidden fields)
        verify_url = url  # Typically submits to same URL
        response = session.post(verify_url, data=form_data)
        
        # Check if verification was successful
        if "ageVerify" not in response.text:
            
            print("Age verification successful!")
        else:
            print(response.text)
            print("Age verification failed")
    else:
        print("No age verification required")
        
if __name__ == '__main__':
    init.init_log()
    age_verify()
    add_subscribe2db("涼森れむ", 123456)