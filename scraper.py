from bs4 import BeautifulSoup
import requests
import sys
import re
import geocoder
import json
import argparse


INSPECTION_DOMAIN = 'http://info.kingcounty.gov'
INSPECTION_PATH = '/health/ehs/foodsafety/inspections/Results.aspx'
INSPECTION_PARAMS = {
    'Output': 'W',
    'Business_Name': '',
    'Business_Address': '',
    'Longitude': '',
    'Latitude': '',
    'City': '',
    'Zip_Code': '',
    'Inspection_Type': 'All',
    'Inspection_Start': '',
    'Inspection_End': '',
    'Inspection_Closed_Business': 'A',
    'Violation_Points': '',
    'Violation_Red_Points': '',
    'Violation_Descr': '',
    'Fuzzy_Search': 'N',
    'Sort': 'H'
}


def get_inspection_page(**kwargs):
    """Return kingcounty inspection page and encoding."""
    url = INSPECTION_DOMAIN + INSPECTION_PATH
    params = INSPECTION_PARAMS.copy()
    for key, value in kwargs.items():
        if key in INSPECTION_PARAMS:
            params[key] = value
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.content, resp.encoding


def load_inspection_page(file):
    """Returns html in load_inspection.html and encoding."""
    with open(file, 'r') as infile:
        html = infile.read()
    return html, 'utf-8'


def parse_source(html, encoding='utf-8'):
    """Decode byte string a tree of python objects or tags."""
    return BeautifulSoup(html, from_encoding=encoding)


def extract_data_listing(html):
    """Extract listing for each restaurant.
    That includes a div tag, and any string that starts with PR, any digits
    with length greater than one and ends with ~."""
    id_finder = re.compile(r'PR[\d]+~')
    return html.find_all('div', id=id_finder)


def has_two_tds(elem):
    is_tr = elem.name == 'tr'
    td_children = elem.find_all('td', recursive=False)
    has_two = len(td_children) == 2
    return is_tr and has_two


def clean_data(td):
    """Remove extraneous characters from visible string."""
    data = td.string
    try:
        return data.strip(" \n:-")
    except AttributeError:
        return u""


def extract_restaurant_metadata(elem):
    """Extract restaurant metadata, where each row contains one restaurant."""
    metadata_rows = elem.find('tbody').find_all(has_two_tds, recursive=False)
    rdata = {}
    current_label = ''
    for row in metadata_rows:
        # there should be two td elements, b/c we filtered above has_two_tds
        key_cell, val_cell = row.find_all('td', recursive=False)
        new_label = clean_data(key_cell)
        current_label = new_label if new_label else current_label
        # if the key doesn't exist, put empty list into value
        # if it doesn't exist append value to existing list
        rdata.setdefault(current_label, []).append(clean_data(val_cell))
    return rdata


def is_inspection_row(elem):
    """Filter if contains tr, 4 children, contains inspection, and
    does not start with inspection. Returns booleans."""
    is_tr = elem.name == 'tr'
    if not is_tr:
        return False
    td_children = elem.find_all('td', recursive=False)
    has_four = len(td_children) == 4
    this_text = clean_data(td_children[0]).lower()
    contains_word = 'inspection' in this_text
    does_not_start = not this_text.startswith('inspection')
    return is_tr and has_four and contains_word and does_not_start


def extract_score_data(elem):
    """Get average, high and num of inspections from each restaurant element."""
    inspection_rows = elem.find_all(is_inspection_row)
    samples = len(inspection_rows)
    total = high_score = average = 0
    for row in inspection_rows:
        strval = clean_data(row.find_all('td')[2])
        try:
            intval = int(strval)
        except (ValueError, TypeError):
            samples -= 1
        else:
            total += intval
            high_score = intval if intval > high_score else high_score
    if samples:
        average = total/float(samples)
    data = {
        u'Average Score': average,
        u'High Score': high_score,
        u'Total Inspections': samples
    }
    return data


def generate_results(test=False, count=10):
    kwargs = {
        'Inspection_Start': '2/1/2013',
        'Inspection_End': '2/1/2015',
        'Zip_Code': '98109'
    }
    if test:
        html, encoding = load_inspection_page('load_inspection.html')
    else:
        html, encoding = get_inspection_page(**kwargs)
    doc = parse_source(html, encoding)
    listings = extract_data_listing(doc)

    for listing in listings[:count]:
        metadata = extract_restaurant_metadata(listing)
        score_data = extract_score_data(listing)
        metadata.update(score_data)
        yield metadata


def get_geojson(result):
    address = " ".join(result.get('Address', ''))
    if not address:
        return None
    geocoded = geocoder.google(address)
    geojson = geocoded.geojson
    inspection_data = {}
    use_keys = (
        'Business Name', 'Average Score', 'Total Inspections', 'High Score',
        'Address',
    )
    for key, val in result.items():
        if key not in use_keys:
            continue
        if isinstance(val, list):
            val = " ".join(val)
        inspection_data[key] = val
    new_address = geojson['properties'].get('address')
    if new_address:
        inspection_data['Address'] = new_address
    geojson['properties'] = inspection_data
    return geojson


def sort_(total_result, sort='average', reverse=False):
    sort_options = {'average': 'Average Score', 'highscore': 'High Score', 'most': 'Total Inspections'}
    return sorted(total_result, reverse=reverse, key=lambda k: k['properties'][sort_options.get(sort)])


if __name__ == '__main__':
    import pprint
    """User must included arguments, sort (average, highscore or most),
    num, and reverse(anything but reverse won't reverse)."""
    parser = argparse.ArgumentParser()
    parser.add_argument('sort')
    parser.add_argument('num', type=int)
    parser.add_argument('reverse')
    args = parser.parse_args()
    if args.reverse == 'reverse':
        reverse = True
    else:
        reverse = False
    total_result = {'type': 'FeatureCollection', 'features': []}
    for result in generate_results(count=args.num):
        geo_result = get_geojson(result)
        total_result['features'].append(geo_result)
    # Sort according to user specificaitons
    total_result['features'] = sort_(total_result['features'], args.sort, reverse)
    with open('my_map.json', 'w') as fh:
        json.dump(total_result, fh)
