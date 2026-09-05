"""
FANS Setup Script — run once after cloning the repo.

Usage:
    python setup.py
"""
import os
import sys
import subprocess
import shutil


def run(cmd, **kwargs):
    print(f'\n>>> {cmd}')
    result = subprocess.run(cmd, shell=True, **kwargs)
    if result.returncode != 0:
        print(f'ERROR: Command failed with code {result.returncode}')
        sys.exit(result.returncode)


def main():
    print('=' * 60)
    print('FANS — Facial Verification System Setup')
    print('=' * 60)

    # 1. Check .env
    if not os.path.exists('.env'):
        shutil.copy('.env.example', '.env')
        print('\n[1] Created .env from .env.example')
        print('    IMPORTANT: Edit .env with your actual values before continuing.')
        print('    Especially: SECRET_KEY, DB_PASSWORD, EMBEDDING_ENCRYPTION_KEY')
    else:
        print('\n[1] .env already exists. Skipping.')

    # 2. Install dependencies
    print('\n[2] Installing Python dependencies...')
    run('pip install -r requirements.txt')

    # 3. Generate encryption key (if not set)
    from dotenv import load_dotenv
    load_dotenv()
    if not os.getenv('EMBEDDING_ENCRYPTION_KEY'):
        print('\n[3] Generating embedding encryption key...')
        run('python manage.py generate_key')
        print('    Add the key above to your .env as EMBEDDING_ENCRYPTION_KEY')
    else:
        print('\n[3] EMBEDDING_ENCRYPTION_KEY already set.')

    # 4. Run migrations
    print('\n[4] Running database migrations...')
    run('python manage.py migrate')

    # 5. Initialize system config
    print('\n[5] Initializing system configuration...')
    run('python manage.py init_config')

    # 6. Create admin user (interactive — no credentials are hard-coded)
    print('\n[6] Creating admin user...')
    print('    Django will prompt for username, email, and password.')
    print('    Choose a strong password and store it securely.')
    # Use subprocess directly so stdin is inherited (interactive prompt works)
    result = subprocess.run(
        [sys.executable, 'manage.py', 'createsuperuser'],
        shell=False,
    )
    if result.returncode != 0:
        print('    WARNING: createsuperuser exited with an error.')
        print('    Create the admin account later with:')
        print('        python manage.py createsuperuser')

    # 7. Collect static files
    print('\n[7] Collecting static files...')
    run('python manage.py collectstatic --noinput')

    print('\n' + '=' * 60)
    print('Setup complete!')
    print('Run the server with: python manage.py runserver')
    print('Login at: http://127.0.0.1:8000/')
    print('Log in with the admin account you created in step 6.')
    print('=' * 60)


if __name__ == '__main__':
    main()
