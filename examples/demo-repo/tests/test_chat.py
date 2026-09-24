from pocketchat.chat import GREETING, Author, Chat, Message


def test_a_new_chat_starts_with_the_greeting() -> None:
    assert Chat().messages == [Message(Author.POCKETCHAT, GREETING)]


def test_a_question_is_followed_by_an_answer() -> None:
    chat = Chat()
    chat.ask("Hello")
    authors = [message.author for message in chat.messages]
    assert authors == [Author.POCKETCHAT, Author.PERSON, Author.POCKETCHAT]
