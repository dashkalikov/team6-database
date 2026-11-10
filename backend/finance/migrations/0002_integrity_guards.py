from django.db import migrations


def provision_existing_wallets(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Wallet = apps.get_model("finance", "Wallet")
    database = schema_editor.connection.alias
    for user_id in User.objects.using(database).values_list("id", flat=True).iterator():
        Wallet.objects.using(database).get_or_create(user_id=user_id, defaults={"balance": 0})


SQL = """
ALTER TABLE rounds ADD CONSTRAINT rounds_wallet_owner
    FOREIGN KEY (wallet_id,user_id) REFERENCES wallets(id,user_id) ON DELETE RESTRICT;
ALTER TABLE coupon_redemptions ADD CONSTRAINT redemptions_wallet_owner
    FOREIGN KEY (wallet_id,user_id) REFERENCES wallets(id,user_id) ON DELETE RESTRICT;
ALTER TABLE wallet_operations ADD CONSTRAINT operations_round_wallet
    FOREIGN KEY (round_id,wallet_id) REFERENCES rounds(id,wallet_id) ON DELETE RESTRICT;
ALTER TABLE wallet_operations ADD CONSTRAINT operations_redemption_wallet
    FOREIGN KEY (redemption_id,wallet_id) REFERENCES coupon_redemptions(id,wallet_id) ON DELETE RESTRICT;

CREATE FUNCTION casi_no_finance_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Financial history is immutable' USING ERRCODE='23514';
END;
$$;
CREATE TRIGGER operations_immutable BEFORE UPDATE OR DELETE ON wallet_operations
    FOR EACH ROW EXECUTE FUNCTION casi_no_finance_immutable();
CREATE TRIGGER rounds_immutable BEFORE UPDATE OR DELETE ON rounds
    FOR EACH ROW EXECUTE FUNCTION casi_no_finance_immutable();
CREATE TRIGGER redemptions_immutable BEFORE UPDATE OR DELETE ON coupon_redemptions
    FOR EACH ROW EXECUTE FUNCTION casi_no_finance_immutable();

CREATE FUNCTION casi_no_wallet_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id,NEW.user_id,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.user_id,OLD.created_at) THEN
        RAISE EXCEPTION 'Wallet identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER wallet_identity BEFORE UPDATE ON wallets
    FOR EACH ROW EXECUTE FUNCTION casi_no_wallet_identity();

CREATE FUNCTION casi_no_coupon_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id,NEW.code,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.code,OLD.created_by,OLD.created_at) THEN
        RAISE EXCEPTION 'Coupon identity is immutable' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER coupon_identity BEFORE UPDATE ON coupons
    FOR EACH ROW EXECUTE FUNCTION casi_no_coupon_identity();

CREATE FUNCTION casi_no_operation_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    stored_balance bigint;
    ledger_balance numeric;
    delta numeric;
BEGIN
    SELECT balance INTO stored_balance FROM wallets WHERE id=NEW.wallet_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Wallet is missing' USING ERRCODE='23503';
    END IF;
    SELECT COALESCE(SUM(CASE WHEN type='bet_debit' THEN -amount ELSE amount END),0)
        INTO ledger_balance FROM wallet_operations WHERE wallet_id=NEW.wallet_id;
    delta := CASE WHEN NEW.type='bet_debit' THEN -NEW.amount ELSE NEW.amount END;
    IF NEW.balance_after<>stored_balance OR NEW.balance_after<>ledger_balance+delta THEN
        RAISE EXCEPTION 'Operation balance does not match wallet and ledger' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER operation_balance BEFORE INSERT ON wallet_operations
    FOR EACH ROW EXECUTE FUNCTION casi_no_operation_balance();

CREATE FUNCTION casi_no_finance_consistent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    wallet_uuid uuid;
    round_uuid uuid;
    redemption_uuid uuid;
    stored_balance bigint;
    ledger_balance numeric;
    round_bet bigint;
    round_payout bigint;
    debit_count bigint;
    credit_count bigint;
    debit_sum numeric;
    credit_sum numeric;
    credited_amount bigint;
BEGIN
    IF TG_TABLE_NAME='wallets' THEN
        wallet_uuid := NEW.id;
    ELSE
        wallet_uuid := NEW.wallet_id;
    END IF;
    SELECT balance INTO stored_balance FROM wallets WHERE id=wallet_uuid FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Wallet is missing' USING ERRCODE='23503';
    END IF;
    SELECT COALESCE(SUM(CASE WHEN type='bet_debit' THEN -amount ELSE amount END),0)
        INTO ledger_balance FROM wallet_operations WHERE wallet_id=wallet_uuid;
    IF stored_balance<>ledger_balance THEN
        RAISE EXCEPTION 'Wallet and ledger differ at commit' USING ERRCODE='23514';
    END IF;

    IF TG_TABLE_NAME='rounds' THEN
        round_uuid := NEW.id;
    ELSIF TG_TABLE_NAME='wallet_operations' THEN
        round_uuid := NEW.round_id;
    END IF;
    IF round_uuid IS NOT NULL THEN
        SELECT bet_amount,payout_amount INTO round_bet,round_payout FROM rounds WHERE id=round_uuid;
        SELECT COUNT(*) FILTER (WHERE type='bet_debit'),COUNT(*) FILTER (WHERE type='win_credit'),
               COALESCE(SUM(amount) FILTER (WHERE type='bet_debit'),0),
               COALESCE(SUM(amount) FILTER (WHERE type='win_credit'),0)
            INTO debit_count,credit_count,debit_sum,credit_sum FROM wallet_operations WHERE round_id=round_uuid;
        IF debit_count<>1 OR debit_sum<>round_bet OR credit_sum<>round_payout
            OR credit_count<>(CASE WHEN round_payout>0 THEN 1 ELSE 0 END) THEN
            RAISE EXCEPTION 'Round and operations differ at commit' USING ERRCODE='23514';
        END IF;
    END IF;

    IF TG_TABLE_NAME='coupon_redemptions' THEN
        redemption_uuid := NEW.id;
    ELSIF TG_TABLE_NAME='wallet_operations' THEN
        redemption_uuid := NEW.redemption_id;
    END IF;
    IF redemption_uuid IS NOT NULL THEN
        SELECT amount INTO credited_amount FROM coupon_redemptions WHERE id=redemption_uuid;
        SELECT COUNT(*),COALESCE(SUM(amount),0) INTO credit_count,credit_sum
            FROM wallet_operations WHERE redemption_id=redemption_uuid AND type='coupon_credit';
        IF credit_count<>1 OR credit_sum<>credited_amount THEN
            RAISE EXCEPTION 'Redemption and operation differ at commit' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
CREATE CONSTRAINT TRIGGER wallet_consistent AFTER INSERT OR UPDATE ON wallets
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION casi_no_finance_consistent();
CREATE CONSTRAINT TRIGGER operations_consistent AFTER INSERT ON wallet_operations
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION casi_no_finance_consistent();
CREATE CONSTRAINT TRIGGER rounds_consistent AFTER INSERT ON rounds
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION casi_no_finance_consistent();
CREATE CONSTRAINT TRIGGER redemptions_consistent AFTER INSERT ON coupon_redemptions
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION casi_no_finance_consistent();
"""


REVERSE_SQL = """
DROP TRIGGER redemptions_consistent ON coupon_redemptions;
DROP TRIGGER rounds_consistent ON rounds;
DROP TRIGGER operations_consistent ON wallet_operations;
DROP TRIGGER wallet_consistent ON wallets;
DROP FUNCTION casi_no_finance_consistent();
DROP TRIGGER operation_balance ON wallet_operations;
DROP FUNCTION casi_no_operation_balance();
DROP TRIGGER coupon_identity ON coupons;
DROP FUNCTION casi_no_coupon_identity();
DROP TRIGGER wallet_identity ON wallets;
DROP FUNCTION casi_no_wallet_identity();
DROP TRIGGER redemptions_immutable ON coupon_redemptions;
DROP TRIGGER rounds_immutable ON rounds;
DROP TRIGGER operations_immutable ON wallet_operations;
DROP FUNCTION casi_no_finance_immutable();
ALTER TABLE wallet_operations DROP CONSTRAINT operations_redemption_wallet;
ALTER TABLE wallet_operations DROP CONSTRAINT operations_round_wallet;
ALTER TABLE coupon_redemptions DROP CONSTRAINT redemptions_wallet_owner;
ALTER TABLE rounds DROP CONSTRAINT rounds_wallet_owner;
"""


class Migration(migrations.Migration):
    dependencies = [("finance", "0001_initial")]
    operations = [
        migrations.RunPython(provision_existing_wallets, reverse_code=migrations.RunPython.noop),
        migrations.RunSQL(SQL, reverse_sql=REVERSE_SQL),
    ]
