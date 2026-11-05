import unittest
from question_parser import QuestionParser, QuestionChoice


class TestQuestionParser(unittest.TestCase):

    def test_informational_list_of_tasks_not_parsed_as_question(self):
        # Samples where agent describes what it will do or what was done
        sample_future = """
Ecco cosa farò adesso per completare l'attività:
1. Configurare il manifest della PWA
2. Aggiornare il service worker per il caching offline
3. Eseguire la suite di test
"""
        self.assertIsNone(QuestionParser.extract_question(sample_future))

        sample_changelog = """
Novità implementate in questo aggiornamento:
1. Supporto allo spegnimento display
2. Nuova gestione del volume e notifiche
"""
        self.assertIsNone(QuestionParser.extract_question(sample_changelog))

        sample_steps = """
I prossimi passi del team sono:
1. Inizializzare il repository
2. Creare i file di test
"""
        self.assertIsNone(QuestionParser.extract_question(sample_steps))

    def test_statement_ending_with_colon_not_parsed_as_question(self):
        sample = """
Dettagli delle modifiche applicate:
1. Modificato main.py
2. Aggiornato question_parser.py
"""
        self.assertIsNone(QuestionParser.extract_question(sample))

    def test_actual_blocking_question_parsed_correctly(self):
        sample_q = """
Quale database desideri utilizzare per il progetto?
1. PostgreSQL con SQLAlchemy
2. SQLite con Peewee
3. MongoDB con Motor
"""
        res = QuestionParser.extract_question(sample_q)
        self.assertIsNotNone(res)
        prompt, choices = res
        self.assertIn("Quale database", prompt)
        self.assertEqual(len(choices), 3)
        self.assertEqual(choices[0].label, "1. PostgreSQL con SQLAlchemy")

    def test_structured_tags_parsed_correctly(self):
        sample_tags = """
[QUESTION: Come desideri procedere con il deployment?]
[OPTION: 1. Deploy in staging]
[OPTION: 2. Deploy in produzione]
"""
        res = QuestionParser.extract_question(sample_tags)
        self.assertIsNotNone(res)
        prompt, choices = res
        self.assertEqual(prompt, "Come desideri procedere con il deployment?")
        self.assertEqual(len(choices), 2)

    def test_yes_no_actual_question(self):
        sample_yn = "Vuoi procedere con la formattazione del disco? [y/n]"
        res = QuestionParser.extract_question(sample_yn)
        self.assertIsNotNone(res)
        prompt, choices = res
        self.assertIn("Vuoi procedere", prompt)
        self.assertEqual(len(choices), 2)

    def test_yes_no_not_triggered_on_statement(self):
        sample_statement = "Il sistema supporta conferme con flag tipo [y/n] o parametri."
        self.assertIsNone(QuestionParser.extract_question(sample_statement))


if __name__ == "__main__":
    unittest.main()
