# -*- coding: utf-8 -*-
import os
import sys
current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
sys.path.append(current_dir)
import requests
from bs4 import BeautifulSoup
import init


def get_movie_cover(query):
    """
    封面抓取
    :param query:
    :return:
    """
    base_url = "https://www.themoviedb.org"
    url = f"https://www.themoviedb.org/search?query={query}"
    headers = {
        "user-agent": init.USER_AGENT,
        "accept-language": "zh-CN"
    }
    response = requests.get(headers=headers, url=url)
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.text, features="html.parser")
    tags_p = soup.findAll('p')
    if "找不到和您的查询相符的电影" in tags_p[1].text:
        return ""
    tags_img = soup.findAll('img')
    image_tag = is_movie_exist(url, tags_img)
    if image_tag is None:
        return ""
    tag_parent = image_tag.find_parent('a')
    if 'href' not in tag_parent.attrs:
        return ""
    main_page = tag_parent['href']
    url = base_url + main_page
    response = requests.get(headers=headers, url=url)
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.text, features="html.parser")
    tags_img = soup.findAll('img')
    if len(tags_img) > 1 and 'src' not in tags_img[1].attrs:
        return ""
    cover_url = tags_img[1]['src']
    return cover_url


# def get_av_cover(query):
#     cover_url = ""
#     headers = {"User-Agent": user_agent,
#                "Cookie": "PHPSESSID=u0h9tqpcm7402cm4vlttoguf60; existmag=mag; age=verified; dv=1",
#                "Upgrade-Insecure-Requests": "1"}
#     response = requests.get(headers=headers, url=f"https://www.javbus.com/search/{query}")
#     if response.status_code == 200:
#         soup = BeautifulSoup(response.text, features="html.parser")
#         container_fluid_div = soup.findAll('div', class_='container-fluid')
#         row_div = container_fluid_div[1].find('div', class_='row')
#         a_tags = row_div.findAll('a', class_='movie-box')
#         for a_tag in a_tags:
#             if 'href' in a_tag.attrs:  # 确保存在 href 属性
#                 if query.lower() in str(a_tag['href']).lower():
#                     img_tag = a_tag.find('img')
#                     cover_url = f"https://www.javbus.com{img_tag['src']}"
#                     break
#     # 尝试搜索无码
#     if response.status_code == 404:
#         response = requests.get(headers=headers, url=f"https://www.javbus.com/uncensored/search/{query}")
#         if response.status_code != 200:
#             return ""
#         soup = BeautifulSoup(response.text, features="html.parser")
#         container_fluid_div = soup.findAll('div', class_='container-fluid')
#         row_div = container_fluid_div[1].find('div', class_='row')
#         a_tags = row_div.findAll('a', class_='movie-box')
#         for a_tag in a_tags:
#             if 'href' in a_tag.attrs:  # 确保存在 href 属性
#                 if query.lower() in str(a_tag['href']).lower():
#                     img_tag = a_tag.find('img')
#                     cover_url = f"https://www.javbus.com{img_tag['src']}"
#                     break
#     return cover_url


def is_movie_exist(url, name_list):
    """
    判断搜索结果是否存在
    :param url:
    :param name_list:
    :return:
    """
    movie_name = url[str(url).index('=') + 1:len(url)]
    img_tag = None
    for name in name_list:
        if 'alt' in name.attrs:
            if name['alt'] == movie_name:
                img_tag = name
                break
    return img_tag


def get_av_cover(query):
    title = ""
    cover_url = ""
    headers = {"user-agent": init.USER_AGENT}
    response = requests.get(headers=headers, url=f"https://avbase.net/works?q={query}")
    soup = BeautifulSoup(response.text, 'html.parser')
    a_tag = soup.find('a', class_='text-md font-bold btn-ghost rounded-lg m-1 line-clamp-5')
    if a_tag:
        title = a_tag.get_text(strip=True)
        link = f"https://avbase.net{a_tag['href']}"
        response = requests.get(headers=headers, url=link)
        soup = BeautifulSoup(response.text, 'html.parser')
        img_tag = soup.find('img', class_='max-w-full max-h-full')
        if img_tag:
            cover_url = img_tag['src']
    if title and cover_url:
        return cover_url, title
    else:
        return "", ""

def is_av_exist(div_list):
    """
    判断搜索结果是否存在
    :param div_list:
    :return:
    """
    is_found = True
    # 倒序遍历提高效率
    for div in reversed(div_list):
        if 'class' in div.attrs:
            if div['class'][0] == 'empty-message':
                is_found = False
                break
    return is_found


if __name__ == '__main__':
    # cover_url = get_movie_cover("阿凡达")
    cover_url, title = get_av_cover("ssis-999")
    print(cover_url, title)