import stanza
from nltk.corpus import wordnet as wn

word_classes = [
    'man', 'woman', 'boy', 'girl', 'person', 'people', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck',
    'boat', 'traffic light', 'fire hydrant', 'sign', 'parking meter', 'bench', 'bird', 'cat', 'dog',
    'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella', 'handbag',
    'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'ball', 'kite', 'baseball bat', 'baseball glove',
    'skateboard', 'surfboard', 'tennis racket', 'bottle', 'glass', 'cup', 'fork', 'knife', 'spoon',
    'bowl', 'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza', 'donut',
    'cake', 'chair', 'couch', 'plant', 'bed', 'table', 'toilet', 'television', 'laptop', 'mouse', 'remote',
    'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock',
    'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush', 'wall', 'sidewalk'
    ]

known_mappings = {
    'rail road track': 'railroad track', 'tv': 'television', 'skate board': 'skateboard'
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
    word = synset.name().lower().split('.')[0]
    if word in word_classes:
        return [word]
    else:
        classes = []
        hypernyms = synset.hypernyms()
        for hypernym in hypernyms:
            classes += find_synset_classes(hypernym)
        return list(set(classes))

def find_phrase_class(phrase):
    if phrase in known_mappings:
        phrase = known_mappings[phrase]
    if phrase in word_classes:
        phrase_class = phrase
    else:
        synsets = wn.synsets(phrase)
        classes = []
        for synset in synsets:
            if get_synset_count(synset) > 0:
                classes += find_synset_classes(synset)
        classes = list(set(classes))
        if len(classes) == 0:
            return None
        else:
            assert len(classes) == 1, f'Phrase "{phrase}" has multiple classes'
            phrase_class = classes[0]
    return phrase_class
    
def extract_noun_spans(token_list):
    noun_spans = []

    # First find sequences of nouns
    noun_sequences = []
    in_sequence = False
    for i in range(len(token_list)):
        if token_list[i][0]['upos'] == 'NOUN' and (not in_sequence):
            sequence_start = i
            in_sequence = True
        if token_list[i][0]['upos'] != 'NOUN' and in_sequence:
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

def find_classes(caption):
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
        if phrase_class is None:
            phrase = token_list[highest_ancestor_ind][0]['text']
            phrase_class = find_phrase_class(phrase)
        classes.append((start_ind, end_ind, phrase_class))
    
    return classes
