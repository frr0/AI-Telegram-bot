import re
from typing import List, Dict, Optional, Tuple

class QuestionChoice:
    def __init__(self, index: int, label: str, value: str):
        self.index = index
        self.label = label
        self.value = value

    def to_dict(self) -> Dict:
        return {
            "index": self.index,
            "label": self.label,
            "value": self.value
        }

class QuestionParser:
    @staticmethod
    def extract_question(text: str) -> Optional[Tuple[str, List[QuestionChoice]]]:
        """
        Parses text to detect questions with multiple choices.
        Returns (question_prompt, list_of_choices) or None if no structured choices found.
        """
        if not text:
            return None

        # Check for structured tags first:
        # [QUESTION: ...]
        # [OPTION: ...] or [CHOICE: ...]
        structured_match = QuestionParser._parse_structured_tags(text)
        if structured_match:
            return structured_match

        # Check for numbered list choices (e.g. 1. ... 2. ...)
        numbered_match = QuestionParser._parse_numbered_choices(text)
        if numbered_match:
            return numbered_match

        # Check for lettered choices (e.g. A) ... B) ...)
        lettered_match = QuestionParser._parse_lettered_choices(text)
        if lettered_match:
            return lettered_match

        # Check for simple Yes/No questions
        yes_no_match = QuestionParser._parse_yes_no(text)
        if yes_no_match:
            return yes_no_match

        return None

    @staticmethod
    def _parse_structured_tags(text: str) -> Optional[Tuple[str, List[QuestionChoice]]]:
        question_match = re.search(r'\[QUESTION[:\s]+(.*?)\]', text, re.IGNORECASE | re.DOTALL)
        options = re.findall(r'\[(?:OPTION|CHOICE)[:\s]+(.*?)\]', text, re.IGNORECASE)
        
        if options and len(options) >= 2:
            question = question_match.group(1).strip() if question_match else "Please choose an option:"
            choices = []
            for i, opt in enumerate(options, 1):
                clean_opt = opt.strip()
                choices.append(QuestionChoice(i, clean_opt, clean_opt))
            return question, choices
        return None

    @staticmethod
    def _parse_numbered_choices(text: str) -> Optional[Tuple[str, List[QuestionChoice]]]:
        # Looks for patterns like:
        # Question text?
        # 1. Option A
        # 2. Option B
        # 3. Option C
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        
        # Find continuous block of numbered items at the end or middle
        choice_lines = []
        pattern = re.compile(r'^(\d+)[\.\)]\s+(.*)$')
        
        for line in lines:
            m = pattern.match(line)
            if m:
                choice_lines.append((int(m.group(1)), m.group(2).strip()))
            else:
                # If we already collected at least 2 choices and hit a non-choice, stop or reset
                if len(choice_lines) >= 2:
                    break
                choice_lines = []

        if len(choice_lines) >= 2:
            # Check numbering sequence (1, 2, ...)
            if choice_lines[0][0] == 1 and choice_lines[1][0] == 2:
                # Find the question prompt line preceding the choices
                first_choice_str = f"1. {choice_lines[0][1]}"
                pos = text.find(first_choice_str)
                if pos == -1:
                    first_choice_str = f"1) {choice_lines[0][1]}"
                    pos = text.find(first_choice_str)

                prompt = text[:pos].strip() if pos != -1 else "Please select an option:"
                # Take the last sentence or paragraph as the direct question
                if "\n" in prompt:
                    prompt_parts = [p.strip() for p in prompt.split("\n") if p.strip()]
                    if prompt_parts:
                        prompt = prompt_parts[-1]

                choices = [
                    QuestionChoice(idx, label, f"{idx}. {label}")
                    for idx, label in choice_lines
                ]
                return prompt or "Select an option:", choices

        return None

    @staticmethod
    def _parse_lettered_choices(text: str) -> Optional[Tuple[str, List[QuestionChoice]]]:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        pattern = re.compile(r'^([A-Fa-f])[\.\)]\s+(.*)$')
        choice_lines = []
        
        for line in lines:
            m = pattern.match(line)
            if m:
                choice_lines.append((m.group(1).upper(), m.group(2).strip()))
            else:
                if len(choice_lines) >= 2:
                    break
                choice_lines = []

        if len(choice_lines) >= 2 and choice_lines[0][0] == 'A' and choice_lines[1][0] == 'B':
            first_label = choice_lines[0][1]
            pos = text.find(first_label)
            prompt = text[:pos].strip() if pos != -1 else "Please select an option:"
            if "\n" in prompt:
                prompt_parts = [p.strip() for p in prompt.split("\n") if p.strip()]
                if prompt_parts:
                    prompt = prompt_parts[-1]

            choices = [
                QuestionChoice(i + 1, f"{letter}) {label}", f"{letter}) {label}")
                for i, (letter, label) in enumerate(choice_lines)
            ]
            return prompt or "Select an option:", choices

        return None

    @staticmethod
    def _parse_yes_no(text: str) -> Optional[Tuple[str, List[QuestionChoice]]]:
        # Matches ending with (Yes/No), [y/n], etc.
        m = re.search(r'([^\n\.\?]+(?:\?|\:)?)\s*[\(\[]\s*(?:y(?:es)?\s*\/\s*n(?:o)?)\s*[\)\]]', text, re.IGNORECASE)
        if m:
            question = m.group(1).strip()
            choices = [
                QuestionChoice(1, "✅ Yes", "Yes"),
                QuestionChoice(2, "❌ No", "No")
            ]
            return question, choices
        return None
