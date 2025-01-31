import stanza
from nltk.corpus import wordnet as wn
import inflect
from transformers import AutoModelForMaskedLM, AutoTokenizer
import torch
from torch import nn
import math
from copy import deepcopy
from utils import all_synsets,\
    phrase2hypernym,\
    phrase2synsets,\
    is_hyponym_of,\
    phrase2replace_str,\
    non_synset_phrases,\
    identical_synsets_mapping,\
    non_inflect_strs

nlp = stanza.Pipeline('en', tokenize_no_ssplit=True)
inflect_engine = inflect.engine()
bert_model = AutoModelForMaskedLM.from_pretrained('bert-large-uncased')
device = torch.device('cuda')
bert_model = bert_model.to(device)
bert_model = bert_model.eval()
tokenizer = AutoTokenizer.from_pretrained('bert-large-uncased')
mask_str = '[MASK]'

def get_synset_count(synset):
    count = 0
    for lemma in synset.lemmas():
        count += lemma.count()
    return count

def identify_synset(synset):
    # Identify whether the synset is in our subtree of the entire WordNet tree (or is a descendant of a node in our subtree)
    if synset.name() in all_synsets:
        return [[synset.name(), 0]]
    if synset.name() in identical_synsets_mapping:
        return [[identical_synsets_mapping[synset.name()], 0]]
    identified_synsets = []
    hypernyms = synset.hypernyms()
    for hypernym in hypernyms:
        cur_identified_synsets = identify_synset(hypernym)
        for i in range(len(cur_identified_synsets)):
            cur_identified_synsets[i][1] += 1
        identified_synsets += cur_identified_synsets
    return identified_synsets

def find_phrase_synsets(phrase):
    phrase = phrase.lower()

    # First, preprocess: if in plural, convert to singular
    if phrase not in non_inflect_strs and inflect_engine.singular_noun(phrase) != False and inflect_engine.singular_noun(phrase) != phrase:
        singular_phrase = inflect_engine.singular_noun(phrase)
        singular_phrase_synsets = find_preprocessed_phrase_synsets(singular_phrase)
        if singular_phrase_synsets is not None and len(singular_phrase_synsets) > 0 and len([x for x in singular_phrase_synsets if x[0] is not None]) > 0:
            return singular_phrase_synsets

    # If ends with possessive s, remove and try
    if phrase.endswith("'s"):
        non_possessive_phrase = phrase[:-2]
        non_possessive_phrase_synsets = find_preprocessed_phrase_synsets(non_possessive_phrase)
        if non_possessive_phrase_synsets is not None and len(non_possessive_phrase_synsets) > 0 and len([x for x in non_possessive_phrase_synsets if x[0] is not None]) > 0:
            return non_possessive_phrase_synsets

    return find_preprocessed_phrase_synsets(phrase)

def search_in_wordnet(phrase):
    phrase_synsets = wn.synsets(phrase)
    phrase_synsets = [synset for synset in phrase_synsets if synset.pos() == 'n']
    identified_synsets = []
    all_synsets_count = sum([get_synset_count(x) for x in phrase_synsets])
    for synset in phrase_synsets:
        if all_synsets_count < 2 or get_synset_count(synset)/all_synsets_count >= 0.1:
            identified_synsets += identify_synset(synset)

    synset_to_lowest_num = {}
    for synset, num in identified_synsets:
        if synset not in synset_to_lowest_num or num < synset_to_lowest_num[synset]:
            synset_to_lowest_num[synset] = num

    synsets = list(synset_to_lowest_num.items())
    if len(synsets) == 0:
        return [(None, 0)]
    else:
        # First, reduce synsets to hyponyms only
        to_remove = {}
        for i in range(len(synsets)):
            for j in range(i+1, len(synsets)):
                if is_hyponym_of(synsets[i][0], synsets[j][0]):
                    to_remove[j] = True
                elif is_hyponym_of(synsets[j][0], synsets[i][0]):
                    to_remove[i] = True
        synsets = [synsets[i] for i in range(len(synsets)) if i not in to_remove]

        # If you have a word that can be refered to both as a fruit and as plant (e.g., 'raspberry') choose a fruit
        strong_synsets = ['edible_fruit.n.01', 'vegetable.n.01', 'edible_nut.n.01', 'flavorer.n.01']
        def is_hyponym_of_strong_synset(phrase):
            for strong_synset in strong_synsets:
                if is_hyponym_of(phrase, strong_synset):
                    return True
            return False
        
        if len(synsets) == 2 and is_hyponym_of_strong_synset(synsets[0][0]) and is_hyponym_of(synsets[1][0], 'plant.n.02'):
            synsets = [synsets[0]]
        if len(synsets) == 2 and is_hyponym_of_strong_synset(synsets[1][0]) and is_hyponym_of(synsets[0][0], 'plant.n.02'):
            synsets = [synsets[1]]

        # If we got 2 synsets, one of which is a hypernym of the other, we'll take the lower one
        if len(synsets) == 2 and is_hyponym_of(synsets[0][0], synsets[1][0]):
            synsets = [synsets[0]]
        elif len(synsets) == 2 and is_hyponym_of(synsets[1][0], synsets[0][0]):
            synsets = [synsets[1]]

    return synsets

def find_preprocessed_phrase_synsets(phrase):
    phrase = phrase.replace(' ', '_')

    ''' Synsets may be found in:
    1. Known mappings from phrases to synsets.
    2. The phrase is in the list of non-synset phrases
    3. WordNet: Search in the wordnet onthology
    '''

    phrase_mappings = []
    if phrase in phrase2synsets:
        direct_synset_mapping = phrase2synsets[phrase]
        phrase_mappings += [(x, 0) for x in direct_synset_mapping]
    if phrase in phrase2hypernym:
        hypernym_mapping = phrase2hypernym[phrase]
        phrase_mappings += hypernym_mapping

    if len(phrase_mappings) > 0:
        # 1. Known mappings
        return phrase_mappings
    elif phrase in non_synset_phrases:
        # Exact mismatch
        return [(None, 0)]
    else:
        # Wordnet
        return search_in_wordnet(phrase)

def preprocess(token_list):
    # Just solving some known issues
    
    # Replace phrases to make the parser's job easier
    replace_dict = [
        # 1. "olive green": olive is considered a noun
        (['olive', 'green'], 'green'),
        # 2. Lionfish: unite, otherwise we will identify a lion
        (['lion', 'fish'], 'lionfish'),
        # 3. Car park: replace, otherwise we will identify a car
        (['car', 'park'], 'park')
    ]

    tokens = [x[0]['text'].lower() for x in token_list]
    inds_in_orig_strs = [0]*len(replace_dict)
    to_replace = []
    for i in range(len(tokens)):
        token = tokens[i]
        for j in range(len(replace_dict)):
            if token == replace_dict[j][0][inds_in_orig_strs[j]]:
                inds_in_orig_strs[j] += 1
                if inds_in_orig_strs[j] == len(replace_dict[j][0]):
                    to_replace.append((i - len(replace_dict[j][0]) + 1, i + 1, replace_dict[j][1]))
                    inds_in_orig_strs[j] = 0
            else:
                inds_in_orig_strs[j] = 0

    if len(to_replace) > 0:
        for start_ind, end_ind, new_str in to_replace:
            tokens[start_ind] = new_str
            tokens[start_ind+1:end_ind] = ['[BLANK]']*(end_ind-(start_ind+1))
        tokens = [x for x in tokens if x != '[BLANK]']
        preprocessed_sentence = ' '.join(tokens)
        doc = nlp(preprocessed_sentence)
        token_lists = [[x.to_dict() for x in y.tokens] for y in doc.sentences]
        token_list = token_lists[0]

    return token_list

def get_probs_from_lm(text, returned_vals):
    input = tokenizer(text, return_tensors='pt', truncation='longest_first').to(device)
    mask_id = tokenizer.vocab[mask_str]
    mask_ind = [i for i in range(input.input_ids.shape[1]) if input.input_ids[0, i] == mask_id][0]
    output = bert_model(**input)
    mask_logits = output.logits[0, mask_ind, :]
    if returned_vals == 'logits':
        return mask_logits
    elif returned_vals == 'probs':
        mask_probs = nn.functional.softmax(mask_logits, dim=0)
        return mask_probs
    else:
        assert False

def is_an_phrase(phrase):
    inflected = inflect_engine.a(phrase)
    return inflected.startswith('an')

def choose_synset_with_lm(token_list, start_ind, end_ind, synset_list, selection_method='probs'):
    synset_to_dist_from_match = {x[0]: x[1] for x in synset_list}
    only_synset_list = [x[0] for x in synset_list]

    before = [x[0]['text'].lower() for x in token_list[:start_ind]]
    after = [x[0]['text'].lower() for x in token_list[end_ind:]]

    orig_phrase = '_'.join([x[0]['text'] for x in token_list[start_ind:end_ind]])

    if orig_phrase.endswith("'s"):
        orig_phrase = orig_phrase[:-2]
    
    plural = False
    if orig_phrase not in non_inflect_strs and inflect_engine.singular_noun(orig_phrase) != False and inflect_engine.singular_noun(orig_phrase) != orig_phrase:
        orig_phrase = inflect_engine.singular_noun(orig_phrase)
        plural = True
    
    if orig_phrase in phrase2replace_str:
        synset_to_repr_phrase = deepcopy(phrase2replace_str[orig_phrase])
    else:
        synset_to_repr_phrase = {synset: wn.synset(synset).lemmas()[0].name() for synset in only_synset_list}
    
    # To prevent unwanted bias, check if we need to consider a/an
    if len(before) > 0 and before[-1] in ['a', 'an']:
        a_synsets = []
        an_synsets = []
        for synset in only_synset_list:
            if is_an_phrase(synset_to_repr_phrase[synset]):
                an_synsets.append(synset)
            else:
                a_synsets.append(synset)
        a_text = ' '.join(before[:-1] + ['a', mask_str] + after)
        a_probs = get_probs_from_lm(a_text, selection_method)
        an_text = ' '.join(before[:-1] + ['an', mask_str] + after)
        an_probs = get_probs_from_lm(an_text, selection_method)
        prob_synset_list = [(a_probs, a_synsets), (an_probs, an_synsets)]
    else:
        text = ' '.join(before + [mask_str] + after)
        probs = get_probs_from_lm(text, selection_method)
        prob_synset_list = [(probs, only_synset_list)]

    max_synset_prob = (-1)*math.inf
    synset_with_max_prob = None
    for probs, synsets in prob_synset_list:
        for synset in synsets:
            repr_phrase = synset_to_repr_phrase[synset]
            if repr_phrase not in tokenizer.vocab:
                # For now, don't handle
                continue
            synset_id = tokenizer.vocab[repr_phrase]
            synset_prob = probs[synset_id]
            if synset_prob > max_synset_prob:
                max_synset_prob = synset_prob
                synset_with_max_prob = synset

    if synset_with_max_prob is None:
        dist_from_match = 0
    else:
        dist_from_match = synset_to_dist_from_match[synset_with_max_prob]
    return synset_with_max_prob, dist_from_match

def is_subtree_first(token_list, ind):
    adjusted_ind = ind + 1
    subtree_first = True
    for cur_ind in range(ind):
        # Check if its an ancestor of ind
        inner_ind = cur_ind
        while True:
            if token_list[inner_ind][0]['head'] == adjusted_ind:
                break
            if token_list[inner_ind][0]['head'] > adjusted_ind or token_list[inner_ind][0]['head'] == 0:
                subtree_first = False
                break
            inner_ind = token_list[inner_ind][0]['head'] - 1
        if not subtree_first:
            break
    return subtree_first

def has_determiner(token_list, ind):
    return len([x for x in token_list if x[0]['head'] == ind+1 and x[0]['upos'] == 'DET']) > 0

def top_handling(token_list, start_ind):
    # Need to distinguish top as a preposition from the clothing
    if len([
            x for x in token_list if x[0]['head'] == start_ind+1 and
            x[0]['upos'] == 'DET' and
            x[0]['text'].lower() in ['a', 'an']
        ]) > 0 and \
        len([
            x for x in token_list if x[0]['head'] == start_ind+1 and
            x[0]['deprel'] == 'compound'
        ]) == 0:
        return [('top.n.10', 0)]
    
    return [(None, 0)]

def water_handling(token_list, start_ind):
    # If there's a "the" before (e.g., "A dolphin swimming in the water") it's the body of water meaning
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] == 'the':
        return [('body_of_water.n.01', 0)]
    
    # If it's part of the phase "body of water" it's a body of water, otherwise let the llm handle it
    if start_ind > 1 and token_list[start_ind - 2][0]['text'] == 'body' and token_list[start_ind - 1][0]['text'] == 'of':
        return [('body_of_water.n.01', 0)]
    
    # Pool/pond is body of water
    if start_ind > 1 and token_list[start_ind - 2][0]['text'] in ['pool', 'pools', 'pond', 'ponds'] and token_list[start_ind - 1][0]['text'] == 'of':
        return [('body_of_water.n.01', 0)]
    
    # Bottle/glass is food
    if start_ind > 1 and token_list[start_ind - 2][0]['text'] in ['bottle', 'bottles', 'glass', 'glasses'] and token_list[start_ind - 1][0]['text'] in ['of', 'with']:
        return [('water.n.06', 0)]
    
     # Some post words is food
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in ['bottle', 'glass', 'bottles', 'glasses', 'cup', 'cups', 'dispenser']:
        return [('water.n.06', 0)]
    
    # Some verbs is food
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in ['steaming', 'drinking', 'drinks', 'mineral']:
        return [('water.n.06', 0)]
    
    # Some post words is body of water
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in ['source', 'fountain', 'flowing', 'surface', 'channel', 'canal', 'channels', 'canals', 'stream']:
        return [('body_of_water.n.01', 0)]
    
    # Some adjectives always mean the body of water meaning
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in ['clear', 'shallow', 'greenish', 'bluish', 'blue', 'sea', 'open', 'lake', 'green', 'river', 'ocean']:
        return [('body_of_water.n.01', 0)]
    
    # "on" before is body of water
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] == 'on':
        return [('body_of_water.n.01', 0)]
    
    # Some post are actually nothing
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in ['slide']:
        return [(None, 0)]
    
    return [('water.n.06', 0), ('body_of_water.n.01', 0)]

def mount_handling(token_list, start_ind):
    # Need to distinguish a name of a mountain from the object used to mount something to the wall
    # If there's a determiner that is a direct child of the node, it is the object
    if len([x for x in token_list if x[0]['head'] == start_ind + 1 and x[0]['upos'] == 'DET']) > 0:
        return [(None, 0)]
    
    return [('mountain.n.01', 0)]

def lemon_handling(token_list, start_ind):
    if (start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] == 'yellow') or \
        (start_ind < len(token_list) - 2 and token_list[start_ind + 1][0]['text'] == '-' and token_list[start_ind + 2][0]['text'] == 'yellow'):
        return [(None, 0)]
    
    return [('lemon.n.01', 0)]

def knife_handling(token_list, start_ind):
    if (start_ind > 1 and token_list[start_ind - 2][0]['text'] in ['fork', 'forks']) or \
        (start_ind < len(token_list) - 2 and token_list[start_ind + 2][0]['text'] in ['fork', 'forks']):
        return [('table_knife.n.01', 0)]
    
    return [('knife.n.02', 0), ('table_knife.n.01', 0)]

def mixer_handling(token_list, start_ind):
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in ['cement', 'concrete']:
        return [(None, 0)]
    
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in ['truck', 'trucks']:
        return [(None, 0)]
    
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in ['music', 'sound', 'dj', 'audio']:
        return [('electronic_equipment.n.01', 1)]
    
    return [('electronic_equipment.n.01', 1), ('kitchen_utensil.n.01', 1)]

def preceding_word_handling_func(token_list, start_ind, preceding_words, synsets_if_applies, synsets_otherwise):
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in preceding_words:
        return synsets_if_applies
    
    return synsets_otherwise

def succeeding_word_handling_func(token_list, start_ind, succeeding_words, synsets_if_applies, synsets_otherwise):
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in succeeding_words:
        return synsets_if_applies
    
    return synsets_otherwise

def preceding_succeeding_word_handling_func(token_list, start_ind, preceding_words, synsets_if_pro_applies, succeeding_words, synsets_if_succ_applies, synsets_otherwise):
    if start_ind > 0 and token_list[start_ind - 1][0]['text'] in preceding_words:
        return synsets_if_pro_applies
    
    if start_ind < len(token_list) - 1 and token_list[start_ind + 1][0]['text'] in succeeding_words:
        return synsets_if_succ_applies
    
    return synsets_otherwise

single_word_to_handling_func = {
    'top': top_handling,
    'tops': top_handling,
    'mixer': mixer_handling,
    'mixers': mixer_handling,
    'couple': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['of'], [(None, 0)], [('couple.n.01', 0)]),
    'couples': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['of'], [(None, 0)], [('couple.n.01', 0)]),
    'pool': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['swimming'], [(None, 0)], [('pond.n.01', 0), ('pool.n.06', 0)]),
    'water': water_handling,
    'bed': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['flower', 'river'], [(None, 0)], [('bed.n.01', 0)]),
    'beds': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['flower', 'river'], [(None, 0)], [('bed.n.01', 0)]),
    'mount': mount_handling,
    'wrap': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['plastic'], [(None, 0)], [('sandwich.n.01', 1)]),
    'plate': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['number', 'license'], [(None, 0)], [('plate.n.04', 0), (None, 0)]),
    'plates': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['number', 'license'], [(None, 0)], [('plate.n.04', 0)]),
    'belt': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['conveyor'], [(None, 0)], [('belt.n.02', 0)]),
    'lemon': lemon_handling,
    'fighter': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['jet', 'jets', 'plane', 'planes'], [('fighter.n.02', 0)], [('person.n.01', 1), ('fighter.n.02', 0)]),
    'fighters': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['jet', 'jets', 'plane', 'planes'], [('fighter.n.02', 0)], [('person.n.01', 1), ('fighter.n.02', 0)]),
    'mouse': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['computer'], [(None, 0)], [('mouse.n.01', 0), (None, 0)]),
    'processor': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['food'], [(None, 0)], [('electronic_equipment.n.01', 1)]),
    'processors': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['food'], [(None, 0)], [('electronic_equipment.n.01', 1)]),
    'player': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['audio', 'music', 'record', 'media', 'digital'], [(None, 0)], [('player.n.01', 0)]),
    'willow': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['house'], [(None, 0)], [('tree.n.01', 1)]),
    'hand': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['second'], [(None, 0)], [('hand.n.01', 0)]),
    'plant': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['power', 'industrial', 'treatment'], [('factory.n.01', 0)], [('plant.n.02', 0)]),
    'plants': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['power', 'industrial'], [('factory.n.01', 0)], [('plant.n.02', 0)]),
    'slide': lambda token_list, start_ind: preceding_succeeding_word_handling_func(token_list, start_ind, ['water'], [('plaything.n.01', 1)], ['projector'], [(None, 0)], [('plaything.n.01', 1), (None, 0)]),
    'slides': lambda token_list, start_ind: preceding_succeeding_word_handling_func(token_list, start_ind, ['water'], [('plaything.n.01', 1)], ['projector'], [(None, 0)], [('plaything.n.01', 1), (None, 0)]),
    'jam': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['traffic'], [(None, 0)], [('nutriment.n.01', 5)]),
    'jams': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['traffic'], [(None, 0)], [('nutriment.n.01', 5)]),
    'hip': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['hop'], [(None, 0)], [('body_part.n.01', 1)]),
    'knife': knife_handling,
    'knives': knife_handling,
    'leg': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['wooden', 'metal', 'iron'], [(None, 0)], [('leg.n.01', 0)]),
    'legs': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['wooden', 'metal', 'iron'], [(None, 0)], [('leg.n.01', 0)]),
    'palm': lambda token_list, start_ind: succeeding_word_handling_func(token_list, start_ind, ['tree', 'trees', 'leaf', 'leaves', 'fruit', 'fruits', 'branch', 'branches'], [('palm.n.03', 2)], [('body_part.n.01', 0)]),
    'head': lambda token_list, start_ind: preceding_word_handling_func(token_list, start_ind, ['shower'], [(None, 0)], [('head.n.01', 0)]),
}

def phrase_location_to_synset(token_list, start_ind, end_ind):
    phrase = ' '.join([token_list[i][0]['text'] for i in range(start_ind, end_ind)]).lower()

    if end_ind - start_ind == 1 and token_list[start_ind][0]['text'] in single_word_to_handling_func:
        synsets = single_word_to_handling_func[token_list[start_ind][0]['text']](token_list, start_ind)

    else:
        synsets = find_phrase_synsets(phrase)

    if len(synsets) > 1:
        synset, dist_from_match = choose_synset_with_lm(token_list, start_ind, end_ind, synsets)
    else:
        synset, dist_from_match = synsets[0]

    return synset, dist_from_match

def is_noun(token_list, ind):
    head_ind = token_list[ind][0]['head'] - 1

    if token_list[ind][0]['upos'] == 'NOUN':
        # VBN edge cases: If we have a noun with a VBN parent (e.g., "flower-covered") the entire phrase is not a noun
        if token_list[head_ind][0]['xpos'] == 'VBN' and token_list[ind][0]['deprel'] == 'compound':
            return False
        
        # "mini" edge case: when used as an adjective parser may call it a noun compound
        if token_list[ind][0]['text'] == 'mini' and token_list[ind][0]['deprel'] == 'compound':
            return False
        
        # uniform edge case: if the word "uniform" follows (e.g., "nurse uniform") this is not a noun
        if ind < len(token_list) - 1 and token_list[ind+1][0]['text'] == 'uniform':
            return False
        
        # glass edge case: if the word "glass" is followed by a noun (e.g., "glass door") this is not a noun
        if token_list[ind][0]['text'] == 'glass' and ind < len(token_list) - 1 and token_list[ind+1][0]['upos'] == 'NOUN':
            return False
        
        # tooth/teeth edge case: if the word "teeth"/"tooth" follows (e.g., "animal teeth") this is not a noun
        if ind < len(token_list) - 1 and token_list[ind+1][0]['text'] in ['teeth', 'tooth']:
            return False
        
        return True
    
    # "remote" edge cases: in many cases, when people say "remote" they mean "remote controller", i.e., a noun. But the
    #  parser treats it as an adjective. To identify these cases, we'll find "remote" with non-noun heads
    if token_list[ind][0]['text'].lower() == 'remote' and token_list[head_ind][0]['upos'] != 'NOUN':
        return True
    
    # "baked goods" edge case: baked is considered adjective, but both should be considered a noun together
    if token_list[ind][0]['text'] == 'baked' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] == 'goods':
        return True
    
    # "orange slices" edge case: orange is considered adjective, but both should be considered a noun together
    if token_list[ind][0]['text'] == 'orange' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] in ['slice', 'slices']:
        return True
    
    # "german shepherd" edge case: german is considered adjective, but both should be considered a noun together
    if token_list[ind][0]['text'] == 'german' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] == 'shepherd':
        return True
    
    # pad thai edge case: if the word "thai" follows pad it's not a verb
    if token_list[ind][0]['text'] == 'pad' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] == 'thai':
        return True
    
    # hot dog: hot is not an adjective
    if token_list[ind][0]['text'] == 'hot' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] in ['dog', 'dogs']:
        return True
    
    # rolling pin: rolling is a noun
    if token_list[ind][0]['text'] == 'rolling' and ind < (len(token_list) - 1) and token_list[ind+1][0]['text'] in ['pin', 'pins']:
        return True
    
    return False

def post_traverse_handling(token_list, start_ind, end_ind, synsets):
    if end_ind - start_ind == 1 and token_list[start_ind][0]['text'] == 'architecture':
        # The word 'architecture' will be considered a building only if no other building was mentioned in the sentence
        if len([synset for synset in synsets if is_hyponym_of(synset[3], 'building.n.01')]) == 0:
            return 'architecture.n.01', 0
        else:
            return None, 0
    
    return None, 0

def postprocessing(synsets):
    # In many cases we have two subsequent nouns referring to the same thing, where one is a hyponym of the second
    # (e.g., "ferry boat"). In this case we want to reduce the two to one
    synsets.sort(key=lambda x:x[0])
    final_synsets = []
    prev_sample = None
    for sample in synsets:
        found_subseqent = False
        while prev_sample is not None and prev_sample[1] == sample[0]:
            if is_hyponym_of(prev_sample[3], sample[3]):
                hyponym = prev_sample
            elif is_hyponym_of(sample[3], prev_sample[3]):
                hyponym = sample
            else:
                break
            found_subseqent = True
            final_synsets = final_synsets[:-1]
            final_synsets.append((prev_sample[0], sample[1], hyponym[2], hyponym[3], hyponym[4]))
            break
        
        if not found_subseqent:
            final_synsets.append(sample)
        prev_sample = sample

    return final_synsets

def find_synsets(caption):
    caption = caption.lower()
    doc = nlp(caption)
    token_lists = [[x.to_dict() for x in y.tokens] for y in doc.sentences]
    if len(token_lists) > 1:
        return None
    token_list = token_lists[0]
    token_list = preprocess(token_list)

    synsets = []

    identified_inds = set()
    # Two word phrases
    i = 0
    while i < len(token_list)-1:
        start_ind = i
        end_ind = i+2
        synset = None
        if is_noun(token_list, i) and is_noun(token_list, i+1):
            synset, dist_from_match = phrase_location_to_synset(token_list, start_ind, end_ind)
        if synset is not None:
            phrase = ' '.join([token_list[i][0]['text'] for i in range(start_ind, end_ind)]).lower()
            synsets.append((start_ind, end_ind, phrase, synset, dist_from_match))
            identified_inds.add(start_ind)
            identified_inds.add(start_ind+1)
            i += 2
        else:
            i += 1

    # Single word phrases
    for i in range(len(token_list)):
        if i in identified_inds:
            continue
        start_ind = i
        end_ind = i+1
        synset = None
        if is_noun(token_list, i):
            synset, dist_from_match = phrase_location_to_synset(token_list, start_ind, end_ind)
        if synset is not None:
            phrase = ' '.join([token_list[i][0]['text'] for i in range(start_ind, end_ind)]).lower()
            synsets.append((start_ind, end_ind, phrase, synset, dist_from_match))
            identified_inds.add(start_ind)

    # Phrases that require handling only once other synsets were identified
    for i in range(len(token_list)):
        if i in identified_inds:
            continue
        start_ind = i
        end_ind = i+1
        synset = None
        if is_noun(token_list, i):
            synset, dist_from_match = post_traverse_handling(token_list, start_ind, end_ind, synsets)
        if synset is not None:
            phrase = ' '.join([token_list[i][0]['text'] for i in range(start_ind, end_ind)]).lower()
            synsets.append((start_ind, end_ind, phrase, synset, dist_from_match))
            identified_inds.add(start_ind)

    synsets = postprocessing(synsets)
    
    return synsets
