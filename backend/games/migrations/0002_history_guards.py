from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("games", "0001_initial")]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE games ADD CONSTRAINT games_current_rules_same_game
                FOREIGN KEY (id, current_rule_version_id)
                REFERENCES game_rule_versions (game_id, id)
                ON DELETE RESTRICT ON UPDATE RESTRICT;

                CREATE FUNCTION casi_no_rules_immutable() RETURNS trigger
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE EXCEPTION 'Game rule versions are immutable'
                        USING ERRCODE = '23514';
                END;
                $$;

                CREATE TRIGGER rules_immutable
                BEFORE UPDATE OR DELETE ON game_rule_versions
                FOR EACH ROW EXECUTE FUNCTION casi_no_rules_immutable();
            """,
            reverse_sql="""
                DROP TRIGGER rules_immutable ON game_rule_versions;
                DROP FUNCTION casi_no_rules_immutable();
                ALTER TABLE games DROP CONSTRAINT games_current_rules_same_game;
            """,
        ),
    ]
