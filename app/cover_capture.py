# -*- coding: utf-8 -*-

import requests
from bs4 import BeautifulSoup

user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def get_movie_cover(query):
    """
    封面抓取
    :param query:
    :return:
    """
    global user_agent
    base_url = "https://www.themoviedb.org"
    url = f"https://www.themoviedb.org/search?query={query}"
    headers = {
        "user-agent": user_agent,
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
    """
    封面抓取
    :param query:
    :return:
    """
    global user_agent
    title = ""
    headers = {"user-agent": user_agent}
    response = requests.get(headers=headers, url=f"https://javdb.com/search?q={query}&f=all")
    if response.status_code != 200:
        return ""
    soup = BeautifulSoup(response.text, features="html.parser")
    div_tags = soup.findAll('div')
    if not is_av_exist(div_tags):
        return ""
    title_tags = soup.findAll('div', class_='video-title')
    for title_tag in title_tags:
        if title_tag.text.lower().find(query.lower()) == 0:
            title = title_tag.text
    img_tags = soup.findAll('img')
    if not img_tags:
        return ""
    if len(img_tags) > 1 and 'src' not in img_tags[1].attrs:
        return ""
    cover_url = img_tags[1]['src']
    return cover_url, title


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
    cover_url = get_movie_cover("阿凡达")
    # cover_url = get_av_cover("ipz-266")
    print(cover_url)