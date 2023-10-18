import stanza
from nltk.corpus import wordnet as wn

word_classes = [
    'man', 'woman', 'boy', 'girl', 'child', 'person', 'people', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train',
    'truck', 'boat', 'traffic light', 'fire hydrant', 'sign', 'parking meter', 'bench', 'bird', 'fish', 'cat', 'dog',
    'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'groundhog', 'pig', 'deer', 'gazelle', 'animal',
    'backpack', 'umbrella', 'handbag', 'tie', 'hat', 'shirt', 'pants', 'dress', 'suitcase', 'frisbee', 'skis', 'snowboard',
    'ball', 'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket', 'plate', 'bottle',
    'glass', 'cup', 'can', 'fork', 'knife', 'spoon', 'bowl', 'tray', 'banana', 'apple', 'sandwich', 'orange', 'broccoli',
    'brussel sprout', 'carrot', 'corn', 'garlic', 'onion', 'sausage', 'vegetable', 'fruit', 'hotdog', 'pizza', 'donut',
    'cake', 'burrito', 'bread', 'coffee', 'chair', 'couch', 'plant', 'bed', 'pillow', 'blanket', 'table', 'counter',
    'toilet', 'television', 'laptop', 'computer', 'monitor', 'mouse', 'remote', 'controller', 'keyboard', 'phone',
    'microwave', 'oven', 'stove', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear',
    'doll', 'hair drier', 'toothbrush', 'wall', 'door', 'windows', 'sidewalk', 'building', 'restaurant', 'mountain',
    'beach', 'kitchen', 'kitchen utensil', 'graffiti', 'tree', 'sky', 'sun', 'moon', 'camera', 'mirror', 'teeth',
    'bathtub', 'wine', 'sea', 'lake', 'mouth', 'ear', 'eye', 'nose', 'platform', 'box', 'uniform', 'towel', 'stone',
    'statue', 'candle', 'rope', 'nut',' bag'
    ]

known_mappings = {
    'rail road track': 'railroad track', 'tv': 'television', 'skate board': 'skateboard', 'roller blades': 'rollerblade',
    'snowboarder': 'person', 'surfer': 'person', 'ocean': 'sea', 'remote-control': 'remote', 'scooter': 'motorcycle',
    'hay': 'plant', 'van': 'car',' walnut': 'nut', 'children': 'child', 'diner': 'restaurant', 'guy': 'man'
}

nlp = stanza.Pipeline('en', tokenize_no_ssplit=True)

def get_depth_at_ind(token_list, i, depths):
    head_ind = token_list[i][0]['head'] - 1
    if head_ind == -1:
        depths[i] = 0
        return depths
    elif depths[head_ind] == -1:
        depths = get_depth_at_ind(token_list, head_ind, depths)
    depths[i] = depths[head_ind] + 1
    return depths

def get_depths(token_list):
    depths = [-1]*len(token_list)
    for i in range(len(token_list)):
        if depths[i] == -1:
            depths = get_depth_at_ind(token_list, i, depths)
    return depths

def get_synset_count(synset):
    count = 0
    for lemma in synset.lemmas():
        count += lemma.count()
    return count

def find_synset_classes(synset):
    classes = []
    for lemma in synset.lemmas():
        word = lemma.name().lower()
        if word in word_classes:
            return [word]
        else:
            cur_classes = []
            hypernyms = synset.hypernyms()
            for hypernym in hypernyms:
                cur_classes += find_synset_classes(hypernym)
            classes += list(set(cur_classes))
    return classes

def find_phrase_class(phrase):
    if phrase in known_mappings:
        phrase = known_mappings[phrase]
    if phrase in word_classes:
        phrase_class = phrase
    else:
        synsets = wn.synsets(phrase)
        classes = []
        all_synsets_count = sum([get_synset_count(x) for x in synsets])
        for synset in synsets:
            if synset.pos() == 'n' and (all_synsets_count == 0 or get_synset_count(synset)/all_synsets_count > 0.2):
                classes += find_synset_classes(synset)
        classes = list(set(classes))
        if len(classes) == 0:
            return None
        else:
            # If you have a word that can be refered to both as a fruit and as plant (e.g., 'raspberry') choose a fruit
            if len(classes) == 2 and 'fruit' in classes and 'plant' in classes:
                classes = 'fruit'

            # Else, we can't except more than one class
            assert len(classes) == 1, f'Phrase "{phrase}" has multiple classes'
            phrase_class = classes[0]

    # Check for plural
    if phrase_class is None and phrase.endswith('s'):
        phrase_class = find_phrase_class(phrase[:-1])
    if phrase_class is None and phrase.endswith('es'):
        phrase_class = find_phrase_class(phrase[:-2])

    return phrase_class

def is_noun(token_list, ind):
    if token_list[ind][0]['upos'] == 'NOUN':
        return True
    
    # "remote" edge cases: in many cases, when people say "remote" they mean "remote controller", i.e., a noun. But the
    #  parser treats it as an adjective. To identify these cases, we'll find "remote" with non-noun heads
    head_ind = token_list[ind][0]['head'] - 1
    if token_list[ind][0]['text'].lower() == 'remote' and token_list[head_ind][0]['upos'] != 'NOUN':
        return True
    
    return False
    
def extract_noun_spans(token_list):
    noun_spans = []

    # First find sequences of nouns
    noun_sequences = []
    in_sequence = False
    for i in range(len(token_list)):
        if is_noun(token_list, i) and (not in_sequence):
            sequence_start = i
            in_sequence = True
        if (not is_noun(token_list, i)) and in_sequence:
            in_sequence = False
            noun_sequences.append((sequence_start, i))
    if in_sequence:
        noun_sequences.append((sequence_start, len(token_list)))

    # Next, for each sequence, find- for each token in the sequence- the highest ancestor inside the sequence
    for sequence_start, sequence_end in noun_sequences:
        highest_ancestors = []
        for token_ind in range(sequence_start, sequence_end):
            cur_ancestor = token_ind
            prev_cur_ancestor = cur_ancestor
            while cur_ancestor >= sequence_start and cur_ancestor < sequence_end:
                prev_cur_ancestor = cur_ancestor
                cur_ancestor = token_list[cur_ancestor][0]['head'] - 1
            highest_ancestors.append(prev_cur_ancestor)
        # A sequence of the same highest ancestor is a noun sequence
        noun_sequence_start = sequence_start
        cur_highest_ancestor = highest_ancestors[0]
        for i in range(1, len(highest_ancestors)):
            if highest_ancestors[i] != cur_highest_ancestor:
                noun_spans.append((noun_sequence_start, sequence_start + i, cur_highest_ancestor))
                noun_sequence_start = sequence_start + i
                cur_highest_ancestor = highest_ancestors[i]
        noun_spans.append((noun_sequence_start, sequence_end, cur_highest_ancestor))

    return noun_spans

def preprocess(caption):
    # Just solving some known issues
    
    # Replace phrases to make the parser's job easier
    replace_dict = {
        # 1. Every time we have 'remote control' in a sentence, 'remote' is an adjective so the identified noun span is
        # 'control', which isn't what we want. So we'll change it to 'remote'
        'remote control': 'remote',
        # 2. "hot dog": hot is considered an adjective, and the only identified noun is "dog"
        'hot dog': 'hotdog',
        'hot dogs': 'hotdogs'
        }

    for orig_str, new_str in replace_dict.items():
        if caption.startswith(orig_str + ' ') or caption.endswith(' ' + orig_str) or ' ' + orig_str + ' ' in caption:
            caption = caption.replace(orig_str, new_str)

    return caption

def find_classes(caption):
    caption = preprocess(caption)
    doc = nlp(caption)
    token_lists = [[x.to_dict() for x in y.tokens] for y in doc.sentences]
    if len(token_lists) > 1:
        return None
    token_list = token_lists[0]

    noun_spans = extract_noun_spans(token_list)
    classes = []

    for start_ind, end_ind, highest_ancestor_ind in noun_spans:
        phrase = ' '.join([token_list[i][0]['text'] for i in range(start_ind, end_ind)]).lower()
        phrase_class = find_phrase_class(phrase)

        # Check only the highest ancestor in the noun span
        if phrase_class is None:
            phrase = token_list[highest_ancestor_ind][0]['text']
            phrase_class = find_phrase_class(phrase)

        # 2. We have a problem when there's a sport named the same as its ball (baseball, basketball etc.).
        # The more common synset is the game, and when someone talks about the ball the algorithm always thinks it's the game.
        # We'll try identifying these cases by checking if it's a single noun and there's an identifier before it
        if phrase_class is None \
            and end_ind - start_ind == 1 \
            and start_ind > 0 \
            and token_list[start_ind-1][0]['upos'] == 'DET' \
            and token_list[start_ind][0]['text'].endswith('ball'):
            phrase_class = 'ball'

        classes.append((start_ind, end_ind, phrase_class))
    
    return classes
