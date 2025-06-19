import csv
import os
from typing import List, Set, Tuple
import pandas as pd
import re
import requests
import time

double_sided_layouts = ['transform', 'modal_dfc']

def read_file(filename):
    with open(filename, 'rb') as f:
        contents = f.read()
    return contents

def cache_and_get_card(card_set: str, card_collector_number: str, card_name: str, is_back: bool) -> bytes:
    card_front_image_query = f"https://api.scryfall.com/cards/{card_set}/{card_collector_number}/?format=image&version=png"
    card_back_image_query = card_front_image_query + "&face=back"

    card_image_query = card_front_image_query if not is_back else card_back_image_query
    
    cache_front_dir = f'game/cache/front/'
    cache_back_dir = f'game/cache/back/'

    os.makedirs(cache_front_dir, exist_ok=True)
    os.makedirs(cache_back_dir, exist_ok=True)

    cache_dir = cache_front_dir if not is_back else cache_back_dir    

    for filename in os.listdir(cache_dir): 
        parts = filename.split('-')
        if len(parts) < 2:
            continue
        set_part = parts[0].lower()
        number_part = parts[1].lower()
        if set_part == card_set.lower() and number_part == card_collector_number.lower():
            return (os.path.splitext(filename)[0], read_file(os.path.join(cache_dir, filename)))

    # We're not cached, fetch the image and cache it
    filename = f'{card_set}-{card_collector_number}-{card_name}.png'
    os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
    card_art = request_scryfall(card_image_query).content
    if card_art is not None:
        image_path = os.path.join(cache_dir, filename)

        with open(image_path, 'wb') as f:
            f.write(card_art)

    return card_art

def request_scryfall(
    query: str,
) -> requests.Response:
    r = requests.get(query, headers = {'user-agent': 'silhouette-card-maker/0.1', 'accept': '*/*'})

    # Check for 2XX response code
    r.raise_for_status()

    # Sleep for 150 milliseconds, greater than the 100ms requested by scryfall API documentation
    time.sleep(0.15)

    return r

def save_card(directory: str, index: int, card_name: str, card_art: bytes, quantity: int) -> None:
    for counter in range(quantity):
        image_path = os.path.join(directory, f'{str(index)}-{str(counter + 1)}-{card_name}.png')

        with open(image_path, 'wb') as f:
            f.write(card_art)

def process_card(
    index: int,
    quantity: int,

    clean_card_name: str,
    card_set: int,
    card_collector_number: int,
    layout: str,

    front_img_dir: str,
    double_sided_dir: str
) -> None:
    (card_name, card_art) = cache_and_get_card(card_set, card_collector_number, clean_card_name, False)
    if card_art is not None:
        save_card(front_img_dir, index, card_name, card_art, quantity)

    # Get backside of card, if it exists
    if layout in double_sided_layouts:
        (card_name, card_art) = cache_and_get_card(card_set, card_collector_number, clean_card_name, True)
        if card_art is not None:
            save_card(double_sided_dir, index, card_name, card_art, quantity)

def remove_non_alphanumeric(s: str) -> str:
    return re.sub(r'[^\w]', '', s)

def format_card_name(s: str) -> str:
    s = re.sub(r'[^\w\s]', '', s)
    s = re.sub(r'\s+', '-', s)
    return s.strip().lower()

def partition_printings(printings: List, condition: List) -> Tuple[List, List]:
    matches = []
    non_matches = []
    for card in printings:
        (matches if condition(card) else non_matches).append(card)
    return matches, non_matches

def progressive_filtering(printings: List, filters):
    pool = printings
    leftovers = []

    for condition in filters:
        matched, not_matched = partition_printings(pool, condition)
        leftovers = not_matched + leftovers
        pool = matched or pool  # Only narrow if we have any matches

    return pool + leftovers

def filtering(printings: List, filters):
    pool = printings

    for condition in filters:
        matched, _ = partition_printings(pool, condition)
        pool = matched

    return pool

def fetch_card(
    index: int,
    quantity: int,

    card_set: str,
    card_collector_number: str,
    ignore_set_and_collector_number: bool,

    name: str,

    prefer_older_sets: bool,
    preferred_sets: Set[str],

    prefer_showcase: bool,
    prefer_extra_art: bool,

    front_img_dir: str,
    double_sided_dir: str
):
    if not ignore_set_and_collector_number and card_set != "" and card_collector_number != "":
        card_info_query = f"https://api.scryfall.com/cards/{card_set}/{card_collector_number}"

        # Query for card info
        card_json = request_scryfall(card_info_query).json()

        process_card(index, quantity, format_card_name(card_json['name']), card_set, card_collector_number, card_json['layout'], front_img_dir, double_sided_dir)

    else:
        if name == "":
            raise Exception()

        # Filter out symbols from card names
        clear_card_name = remove_non_alphanumeric(name)

        card_info_query = f'https://api.scryfall.com/cards/named?exact={clear_card_name}'

        # Query for card info
        card_json = request_scryfall(card_info_query).json()

        set = card_json["set"]
        collector_number = card_json["collector_number"]

        # If preferred options are used, then filter over prints
        if prefer_older_sets or len(preferred_sets) > 0 or prefer_showcase or prefer_extra_art:
            # Get available printings
            prints_search_json = request_scryfall(card_json['prints_search_uri']).json()
            card_printings = prints_search_json['data']

            # Optional reverse for older preferences
            if prefer_older_sets:
                card_printings.reverse()

            # Define filters in order of preference
            filters = [
                lambda c: c['nonfoil'],
                lambda c: not c['digital'],
                lambda c: not c['promo'],
                lambda c: c['set'] in preferred_sets,
                lambda c: not prefer_showcase ^ ('frame_effects' in c and 'showcase' in c['frame_effects']),
                lambda c: not prefer_extra_art ^ (c['full_art'] or c['border_color'] == "borderless" or ('frame_effects' in c and 'extendedart' in c['frame_effects']))
            ]

            # Apply progressive filtering
            filtered_printings = progressive_filtering(card_printings, filters)

            if len(filtered_printings) == 0:
                print(f'No printings found for "{name}" with preferred options. Using default instead.')
            else:
                best_print = filtered_printings[0]
                set = best_print["set"]
                collector_number = best_print["collector_number"]

        # Fetch card art
        process_card(
            index,
            quantity,
            format_card_name(card_json['name']),
            set,
            collector_number,
            card_json['layout'],
            front_img_dir,
            double_sided_dir
        )

def get_handle_card(
    ignore_set_and_collector_number: bool,

    prefer_older_sets: bool,
    preferred_sets: Set[str],

    prefer_showcase: bool,
    prefer_extra_art: bool,

    front_img_dir: str,
    double_sided_dir: str
):
    def configured_fetch_card(index: int, name: str, card_set: str = None, card_collector_number: int = None, quantity: int = 1):
        fetch_card(
            index,
            quantity,

            card_set,
            card_collector_number,
            ignore_set_and_collector_number,

            name,

            prefer_older_sets,
            preferred_sets,

            prefer_showcase,
            prefer_extra_art,

            front_img_dir,
            double_sided_dir
        )
    return configured_fetch_card