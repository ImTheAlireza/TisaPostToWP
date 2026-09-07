import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parser import normalize_caption

SAMPLE = '''
قاب Stitch Lovely 💙🧵
قاب شفاف پشت طلق 💙
ایرپاد شفاف ست برای 💙 1/2/3/4/pro/pro2💙

Apple :
📱iphone 7/8
📱iphone 7+/8+
📱iphone X
📱iphone Xsmax
📱iphone 11
📱iphone 11 pro
📱iphone 11 promax
📱iphone 12
📱iphone 12 pro
📱iphone 12 promax
📱iphone 13
📱iphone 13 pro
📱iphone 13 Promax 
📱iphone 14Pro
📱iphone 14Promax
📱iphone 15
📱iphone 15Pro
📱iphone 15Promax
📱iphone 16
📱iphone 16pro
📱iphone 16Promax
📱iphone 17 
📱iphone 17 Air 
📱iphone 17 pro 
📱iphone 17 promax 

SAMSUNG 
📱S25 ultra 
📱S24 ultra 
📱S23 ultra 
📱S22 ultra 
📱S25 fe
📱S24 fe
📱S23 fe
📱S21 fe
📱S20 fe
📱A73
📱A72
📱A71
📱A56
📱A55
📱A54
📱A53
📱A52
📱A51
📱A50
📱A36
📱A35
📱A34
📱A33
📱A32 4G
📱A31
📱A25
📱A22 4G
📱A21 s
📱A17
📱A16
📱A15
📱A14
📱A13
📱A12
📱A11
📱A06

Xiaomi 
📱Note 8 pro
📱Note 9 pro
📱Note 11 pro
📱Note 12s
📱Note12 4G
📱Note 12 pro
📱Note 13 
📱Note 13 pro
📱Note 13 pro plus
📱Note 14 
📱Note 14 pro⁩⁩
'''

EXPECTED = (
    'iPhone 7/8 | iPhone 7 Plus/8 Plus | iPhone X | iPhone XS Max | '
    'iPhone 11 | iPhone 11 Pro | iPhone 11 Pro Max | iPhone 12 | iPhone 12 Pro | '
    'iPhone 12 Pro Max | iPhone 13 | iPhone 13 Pro | iPhone 13 Pro Max | '
    'iPhone 14 Pro | iPhone 14 Pro Max | iPhone 15 | iPhone 15 Pro | iPhone 15 Pro Max | '
    'iPhone 16 | iPhone 16 Pro | iPhone 16 Pro Max | iPhone 17 | iPhone 17 Air | '
    'iPhone 17 Pro | iPhone 17 Pro Max | '
    'S20 FE | S21 FE | S22 Ultra | S23 FE | S23 Ultra | S24 FE | S24 Ultra | S25 FE | S25 Ultra | '
    'A06 | A11 | A12 | A13 | A14 | A15 | A16 | A17 | A21s | A22 4G | A25 | A31 | A32 4G | '
    'A33 | A34 | A35 | A36 | A50 | A51 | A52 | A53 | A54 | A55 | A56 | A71 | A72 | A73 | '
    'Redmi Note 8 Pro | Redmi Note 9 Pro | Redmi Note 11 Pro | Redmi Note 12 4G | Redmi Note 12 S | Redmi Note 12 Pro | Redmi Note 13 | '
    'Redmi Note 13 Pro | Redmi Note 13 Pro Plus | Redmi Note 14 | Redmi Note 14 Pro'
)


def test_real_world_sample() -> None:
    assert normalize_caption(SAMPLE) == EXPECTED


def test_messy_variants() -> None:
    text = 'Apple: iPhone17promax\nSAMSUNG: A21 s\nXiaomi: Redmi Note12 4G'
    assert normalize_caption(text) == 'iPhone 17 Pro Max | A21s | Redmi Note 12 4G'


def test_same_compatibility_group_is_one_item() -> None:
    text = 'Apple:\niphone 7+/8+'
    assert normalize_caption(text) == 'iPhone 7 Plus/8 Plus'


def test_generic_xiaomi_prefix_is_removed_but_redmi_is_kept() -> None:
    text = 'Xiaomi\nNote12 4G\nRedmi Note 12 4G'
    assert normalize_caption(text) == 'Redmi Note 12 4G'


def test_marketing_prose_is_ignored() -> None:
    text = 'قاب خاص برای ایرپاد 1/2/3/4/pro/pro2 🔥\nApple:\niphone 13 promax'
    assert normalize_caption(text) == 'iPhone 13 Pro Max'
