class ScriptedOperator:
    def __init__(self, secrets: list[str], agrees: bool) -> None:
        self.can_ask = True
        self.secrets = secrets
        self.agrees = agrees
        self.prompts: list[str] = []
        self.questions: list[str] = []

    def secret(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.secrets.pop(0)

    def agree(self, question: str) -> bool:
        self.questions.append(question)
        return self.agrees
