"""Pre-written persona lines. The persona changes the tone only; numbers are filled in by code.

Used for the reminder, the night verdict, the monthly report and the /persona sample, and as the
fallback when Claude is unavailable (normally persona_llm writes the reply to each entry).
Situations: report, expense, big, income, no_spend, reminder.
Tone: 'soft' for roast 0-1, 'spicy' for roast 2-3.
"""
from __future__ import annotations

import random
from typing import Optional

PERSONAS = {
    "cat": "😼 Sassy Cat",
    "mom": "👩 Asian Mom",
    "monk": "🧘 Zen Monk",
    "plain": "📋 Plain (no comments)",
}

ROAST_LABELS = {0: "0 · gentle", 1: "1 · friendly", 2: "2 · cheeky", 3: "3 · savage"}
LANGUAGES = {"en": "English", "vi": "Tiếng Việt", "mix": "Mix"}

LINES: dict[str, dict[str, dict[str, dict[str, list[str]]]]] = {
    "cat": {
        "report": {
            "soft": {"en": ["A new month! Here's what happened to the treats budget. 🐾"], "vi": ["Tháng mới rồi! Xem tháng trước mình tiêu gì nha. 🐾"]},
            "spicy": {"en": ["Monthly audit. I've seen everything. Everything. 😼"], "vi": ["Kiểm toán cuối tháng. Mèo thấy hết rồi đó. 😼"]},
        },
        "expense": {
            "soft": {"en": ["Noted. I'm watching. 🐾", "Purr-fectly logged.", "Into the ledger it goes. 😺"],
                     "vi": ["Ghi rồi nha. Mèo đang theo dõi đó. 🐾", "Xong! Sổ sách gọn gàng. 😺"]},
            "spicy": {"en": ["Another one? I'm judging you from the windowsill. 😼", "Fine. I'll pretend I didn't see that.",
                             "Your wallet just hissed."],
                      "vi": ["Lại tiêu nữa hả? Mèo nhìn thấy hết đó. 😼", "Ví của bạn vừa kêu meo meo vì đau."]},
        },
        "big": {
            "soft": {"en": ["{amount}? That's a lot of tuna. Hope it was worth it. 🐟"],
                     "vi": ["{amount}? Bằng cả kho cá ngừ đó. Mong là đáng nha. 🐟"]},
            "spicy": {"en": ["{amount}?! I knocked a glass off the table for less. 🙀", "{amount}. Bold. Reckless. Very cat of you."],
                      "vi": ["{amount}?! Mèo hất ly nước xuống đất còn đỡ hơn. 🙀"]},
        },
        "income": {
            "soft": {"en": ["Money in! Treats for everyone? (No.) 😻"], "vi": ["Tiền về! Thưởng mèo một pate nhé? 😻"]},
            "spicy": {"en": ["Income! Don't you dare spend it all on boxes I'll ignore."], "vi": ["Có tiền rồi đó. Đừng tiêu hết vào mấy cái hộp mèo không thèm chơi."]},
        },
        "no_spend": {
            "soft": {"en": ["A no-spend day. I'm almost impressed. 😻"], "vi": ["Một ngày không tiêu đồng nào. Mèo hơi bị nể. 😻"]},
            "spicy": {"en": ["Zero spent? Who are you and what did you do with my human?"], "vi": ["Không tiêu gì? Bạn là ai, trả lại sen của mèo đây!"]},
        },
        "reminder": {
            "soft": {"en": ["🐱 Meow. Nothing logged today. What did you spend?"], "vi": ["🐱 Meo. Hôm nay chưa ghi gì hết. Tiêu gì rồi kể nghe?"]},
            "spicy": {"en": ["😼 Silence all day? Suspicious. What did you buy?"], "vi": ["😼 Im lặng cả ngày? Đáng ngờ lắm nha. Mua gì rồi?"]},
        },
    },
    "mom": {
        "report": {
            "soft": {"en": ["Month done! Let Mom see how you did."], "vi": ["Hết tháng rồi! Cho mẹ xem con tiêu thế nào nào."]},
            "spicy": {"en": ["Report card time. Don't hide anything from Mom."], "vi": ["Đến giờ xem bảng điểm. Không được giấu mẹ cái gì đâu."]},
        },
        "expense": {
            "soft": {"en": ["OK, Mom wrote it down. 📒", "Good, you're keeping track. Mom is proud."],
                     "vi": ["Rồi, mẹ ghi lại rồi nha. 📒", "Ngoan, biết ghi chép là tốt."]},
            "spicy": {"en": ["You could have cooked at home, you know.", "Again? Money doesn't grow on trees!"],
                      "vi": ["Ở nhà nấu ăn có phải đỡ tốn không con.", "Lại tiêu nữa! Tiền đâu phải lá mít!"]},
        },
        "big": {
            "soft": {"en": ["{amount}... I hope you really needed it."], "vi": ["{amount}... mong là con thật sự cần nha."]},
            "spicy": {"en": ["{amount}?! When I was your age, that was a month of rice!", "{amount}? Your cousin is saving for a house, you know."],
                      "vi": ["{amount}?! Hồi mẹ bằng tuổi con, số đó ăn cả tháng!", "{amount}? Con nhà người ta đang để dành mua nhà đó."]},
        },
        "income": {
            "soft": {"en": ["Good job! Save some first, OK?"], "vi": ["Giỏi lắm! Nhớ để dành trước nha con."]},
            "spicy": {"en": ["Salary came in. Now don't spend it all by Friday."], "vi": ["Lương về rồi đó. Đừng có tiêu hết trước thứ Sáu nghe chưa."]},
        },
        "no_spend": {
            "soft": {"en": ["No spending today! Mom is so proud. 🥹"], "vi": ["Hôm nay không tiêu gì! Mẹ tự hào lắm. 🥹"]},
            "spicy": {"en": ["No spending? Finally! Tell your cousin."], "vi": ["Không tiêu gì hả? Cuối cùng cũng biết điều! Khoe với họ hàng liền."]},
        },
        "reminder": {
            "soft": {"en": ["👩 Have you eaten? And what did you spend today?"], "vi": ["👩 Ăn cơm chưa con? Hôm nay tiêu gì rồi?"]},
            "spicy": {"en": ["👩 You didn't write anything today. Don't hide it from Mom."], "vi": ["👩 Hôm nay chưa ghi gì hết. Đừng có giấu mẹ."]},
        },
    },
    "monk": {
        "report": {
            "soft": {"en": ["A month has passed. Let us look at it calmly. 🧘"], "vi": ["Một tháng đã trôi qua. Hãy cùng nhìn lại thật bình thản. 🧘"]},
            "spicy": {"en": ["To see where the money went is the first step to letting it stay."], "vi": ["Biết tiền đi đâu là bước đầu để giữ nó lại."]},
        },
        "expense": {
            "soft": {"en": ["Noted. Breathe in, breathe out. 🍃", "Each coin has its path."],
                     "vi": ["Đã ghi. Hít vào, thở ra. 🍃", "Mỗi đồng tiền đều có con đường của nó."]},
            "spicy": {"en": ["Was it want, or was it need? 🍃", "The wallet that is always open is soon empty."],
                      "vi": ["Đó là muốn, hay là cần? 🍃", "Ví lúc nào cũng mở thì sớm muộn cũng rỗng."]},
        },
        "big": {
            "soft": {"en": ["{amount}. May it bring you lasting peace."], "vi": ["{amount}. Mong nó mang lại bình an lâu dài."]},
            "spicy": {"en": ["{amount}. Attachment is the root of suffering. And of credit card bills."],
                      "vi": ["{amount}. Chấp niệm là gốc của khổ đau. Và của hóa đơn thẻ tín dụng."]},
        },
        "income": {
            "soft": {"en": ["Abundance arrives. Receive it calmly. 🙏"], "vi": ["Tài lộc đến. Hãy đón nhận thật bình thản. 🙏"]},
            "spicy": {"en": ["Money flows in. Let it not flow straight out."], "vi": ["Tiền chảy vào. Đừng để nó chảy ra ngay."]},
        },
        "no_spend": {
            "soft": {"en": ["A day of stillness. The mind and the wallet rest. 🧘"], "vi": ["Một ngày tĩnh lặng. Tâm và ví đều được nghỉ ngơi. 🧘"]},
            "spicy": {"en": ["Nothing spent. Nothing lacking. Enlightenment is near."], "vi": ["Không tiêu gì. Không thiếu gì. Giác ngộ đã gần."]},
        },
        "reminder": {
            "soft": {"en": ["🧘 The day is ending. What passed through your hands?"], "vi": ["🧘 Ngày sắp tàn. Hôm nay tiền đã đi qua tay bạn thế nào?"]},
            "spicy": {"en": ["🧘 An unrecorded day is an unexamined day. What did you spend?"], "vi": ["🧘 Một ngày không ghi chép là một ngày không tỉnh thức. Bạn đã tiêu gì?"]},
        },
    },
}


def line(persona: str, roast: int, language: str, situation: str,
         rng: Optional[random.Random] = None, **facts) -> Optional[str]:
    """One persona line, or None for the plain persona or an unknown situation."""
    rng = rng or random
    by_situation = LINES.get(persona, {}).get(situation)
    if not by_situation:
        return None
    if roast == 0 and situation == "expense":
        return None  # gentle mode stays quiet on everyday entries
    tone = "spicy" if roast >= 2 else "soft"
    lang = rng.choice(["en", "vi"]) if language == "mix" else language
    options = by_situation[tone].get(lang) or by_situation[tone]["en"]
    return rng.choice(options).format(**facts)
