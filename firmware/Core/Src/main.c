/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "cmsis_os.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

#define MPU6050_ADDR 0xD0

// Flash Black Box Defines
#define FLASH_USER_SECTOR_ADDR   0x08020000     // Start of Sector 5
#define FLASH_USER_SECTOR        FLASH_SECTOR_5
#define CRASH_MAGIC_WORD         0xDEADBEEF     // Marker to verify log existence
#define CRASH_THRESHOLD_SQ       2.25f          // 1.5g squared (avoids an expensive sqrt() call)
#define TELEMETRY_RATE_VISUAL_MS 30U
#define TELEMETRY_RATE_FAST_MS   500U
#define TELEMETRY_RATE_NORMAL_MS 1000U
#define TELEMETRY_RATE_SLOW_MS   2000U
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
I2C_HandleTypeDef hi2c1;

IWDG_HandleTypeDef hiwdg;

UART_HandleTypeDef huart2;
DMA_HandleTypeDef hdma_usart2_tx;

/* Definitions for TelemetryTask */
osThreadId_t TelemetryTaskHandle;
const osThreadAttr_t TelemetryTask_attributes = {
  .name = "TelemetryTask",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityNormal,
};
/* Definitions for StatusTask */
osThreadId_t StatusTaskHandle;
const osThreadAttr_t StatusTask_attributes = {
  .name = "StatusTask",
  .stack_size = 256 * 4,
  .priority = (osPriority_t) osPriorityBelowNormal,
};
/* Definitions for cmdQueue */
osMessageQueueId_t cmdQueueHandle;
const osMessageQueueAttr_t cmdQueue_attributes = {
  .name = "cmdQueue"
};
/* Definitions for uartMutex */
osMutexId_t uartMutexHandle;
const osMutexAttr_t uartMutex_attributes = {
  .name = "uartMutex"
};
/* USER CODE BEGIN PV */

// MPU6050 Raw Data
int16_t Accel_X_RAW = 0;
int16_t Accel_Y_RAW = 0;
int16_t Accel_Z_RAW = 0;
int16_t Gyro_X_RAW = 0;
int16_t Gyro_Y_RAW = 0;
int16_t Gyro_Z_RAW = 0;

// MPU6050 Processed Data
float Ax, Ay, Az;
float Gx, Gy, Gz;

// Status Variables
uint8_t check = 0;
uint8_t mpu_init_success = 0;
uint8_t rx_byte = 0;

// Telemetry Stream Control Flags
volatile uint8_t telemetry_paused = 0;      // 0 = Streaming, 1 = Paused
volatile uint32_t telemetry_delay_ms = TELEMETRY_RATE_VISUAL_MS;
volatile uint32_t command_rx_count = 0;
volatile uint32_t command_rx_drop_count = 0;

// Crash Detection Flag
volatile uint8_t crash_logged = 0;

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_DMA_Init(void);
static void MX_I2C1_Init(void);
static void MX_USART2_UART_Init(void);
static void MX_IWDG_Init(void);
void StartDefaultTask(void *argument);
void StartStatusTask(void *argument);

/* USER CODE BEGIN PFP */

// MPU6050 Functions
void MPU6050_Init(void);
void MPU6050_Read_All(void);
void MPU6050_Sleep(void);
void MPU6050_Wake(void);

uint8_t calculate_checksum(char* str, int len);

void Clear_Crash_Log(void);
void Write_Crash_Log(float x, float y, float z);

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

/* ============================================================================
   MPU6050 INITIALIZATION
   ============================================================================ */
void MPU6050_Init(void)
{
    uint8_t Data;
    HAL_StatusTypeDef status;

    HAL_Delay(100);

    // Check device ID
    status = HAL_I2C_Mem_Read(&hi2c1, MPU6050_ADDR, 0x75, 1, &check, 1, 1000);

    if (status != HAL_OK || check != 0x68)
    {
        mpu_init_success = 0;
        return;
    }

    mpu_init_success = 1;

    // Wake up using the X gyro PLL as the clock reference.
    Data = 0x01;
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x6B, 1, &Data, 1, 1000);
    HAL_Delay(10);

    // 44 Hz digital low-pass filter and 200 Hz sensor sample rate.  The host
    // can still receive a lighter 33 Hz visual stream, but each sample is
    // cleaner and the sensor has a much better chance of observing fast tilt.
    Data = 0x03;
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x1A, 1, &Data, 1, 1000);
    Data = 0x04;
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x19, 1, &Data, 1, 1000);

    // ±1000 °/s avoids clipping during quick hand rotations (32.8 LSB/°/s).
    Data = 0x10;
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x1B, 1, &Data, 1, 1000);

    // ±4 g retains normal hand-motion resolution without clipping shocks.
    Data = 0x08;
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x1C, 1, &Data, 1, 1000);

    HAL_Delay(50);
}

/* ============================================================================
   MPU6050 SYNCHRONIZED ACCELEROMETER + GYROSCOPE READ
   ============================================================================ */
void MPU6050_Read_All(void)
{
    uint8_t Rec_Data[14];
    HAL_StatusTypeDef status;

    // One 14-byte burst keeps acceleration and gyro values from the same IMU
    // sample interval, rather than mixing two independent I2C reads.
    status = HAL_I2C_Mem_Read(&hi2c1, MPU6050_ADDR, 0x3B, 1, Rec_Data, 14, 1000);
    if (status != HAL_OK) {
        return;
    }

    Accel_X_RAW = (int16_t)(Rec_Data[0] << 8 | Rec_Data[1]);
    Accel_Y_RAW = (int16_t)(Rec_Data[2] << 8 | Rec_Data[3]);
    Accel_Z_RAW = (int16_t)(Rec_Data[4] << 8 | Rec_Data[5]);
    Gyro_X_RAW = (int16_t)(Rec_Data[8] << 8 | Rec_Data[9]);
    Gyro_Y_RAW = (int16_t)(Rec_Data[10] << 8 | Rec_Data[11]);
    Gyro_Z_RAW = (int16_t)(Rec_Data[12] << 8 | Rec_Data[13]);

    Ax = (float)Accel_X_RAW / 8192.0f;
    Ay = (float)Accel_Y_RAW / 8192.0f;
    Az = (float)Accel_Z_RAW / 8192.0f;
    Gx = (float)Gyro_X_RAW / 32.8f;
    Gy = (float)Gyro_Y_RAW / 32.8f;
    Gz = (float)Gyro_Z_RAW / 32.8f;
}

/* ============================================================================
   MPU6050 POWER MANAGEMENT FUNCTIONS
   ============================================================================ */

// Put MPU6050 into Low-Power Sleep Mode (5 uA)
void MPU6050_Sleep(void)
{
    uint8_t Data = 0x40; // Set Bit 6 (SLEEP = 1)
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x6B, 1, &Data, 1, 1000);
}

// Wake up MPU6050 to Active Sampling Mode (~3.9 mA)
void MPU6050_Wake(void)
{
    uint8_t Data = 0x00; // Clear Bit 6 (SLEEP = 0, CLKSEL = Internal 8MHz)
    HAL_I2C_Mem_Write(&hi2c1, MPU6050_ADDR, 0x6B, 1, &Data, 1, 1000);
    HAL_Delay(10);       // Allow internal PLL and sensors to stabilize
}

/* ============================================================================
   NON-VOLATILE FLASH LOGGING FUNCTIONS
   ============================================================================ */

void Clear_Crash_Log(void)
{
    HAL_IWDG_Refresh(&hiwdg); // Keep watchdog happy before blocking erase

    HAL_FLASH_Unlock();

    FLASH_EraseInitTypeDef EraseInitStruct;
    uint32_t SectorError = 0;

    EraseInitStruct.TypeErase = FLASH_TYPEERASE_SECTORS;
    EraseInitStruct.VoltageRange = FLASH_VOLTAGE_RANGE_3; // 2.7V - 3.6V (Nucleo 3.3V supply)
    EraseInitStruct.Sector = FLASH_USER_SECTOR;
    EraseInitStruct.NbSectors = 1;

    HAL_FLASHEx_Erase(&EraseInitStruct, &SectorError);

    HAL_FLASH_Lock();

    HAL_IWDG_Refresh(&hiwdg); // Refresh watchdog immediately after erase
}

void Write_Crash_Log(float x, float y, float z)
{
    Clear_Crash_Log(); // Erase sector first (resets bits to 0xFFFFFFFF)

    HAL_FLASH_Unlock();

    // Re-interpret float bit patterns as 32-bit words
    uint32_t valX = *(uint32_t*)&x;
    uint32_t valY = *(uint32_t*)&y;
    uint32_t valZ = *(uint32_t*)&z;

    // Write Magic Word and Kinematic Data
    HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, FLASH_USER_SECTOR_ADDR, CRASH_MAGIC_WORD);
    HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, FLASH_USER_SECTOR_ADDR + 4, valX);
    HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, FLASH_USER_SECTOR_ADDR + 8, valY);
    HAL_FLASH_Program(FLASH_TYPEPROGRAM_WORD, FLASH_USER_SECTOR_ADDR + 12, valZ);

    HAL_FLASH_Lock();
}

/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_DMA_Init();
  MX_I2C1_Init();
  MX_USART2_UART_Init();
  MX_IWDG_Init();
  /* USER CODE BEGIN 2 */

  MPU6050_Init();
  // Check if Flash Sector 5 already contains a saved crash log from a previous run
  uint32_t boot_magic = *(__IO uint32_t*)FLASH_USER_SECTOR_ADDR;
  if (boot_magic == CRASH_MAGIC_WORD)
  {
      HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_SET); // Restore LED ON state
      crash_logged = 1; // Sync RAM flag with Flash memory state
  }

  /* USER CODE END 2 */

  /* Init scheduler */
  osKernelInitialize();
  /* Create the mutex(es) */
  /* creation of uartMutex */
  uartMutexHandle = osMutexNew(&uartMutex_attributes);

  /* USER CODE BEGIN RTOS_MUTEX */
  /* add mutexes, ... */
  /* USER CODE END RTOS_MUTEX */

  /* USER CODE BEGIN RTOS_SEMAPHORES */
  /* add semaphores, ... */
  /* USER CODE END RTOS_SEMAPHORES */

  /* USER CODE BEGIN RTOS_TIMERS */
  /* start timers, add new ones, ... */
  /* USER CODE END RTOS_TIMERS */

  /* Create the queue(s) */
  /* creation of cmdQueue */
  cmdQueueHandle = osMessageQueueNew (10, sizeof(uint8_t), &cmdQueue_attributes);

  // Arm UART RX only after the ISR's destination queue exists.
  HAL_UART_Receive_IT(&huart2, &rx_byte, 1);

  /* USER CODE BEGIN RTOS_QUEUES */
  /* add queues, ... */
  /* USER CODE END RTOS_QUEUES */

  /* Create the thread(s) */
  /* creation of TelemetryTask */
  TelemetryTaskHandle = osThreadNew(StartDefaultTask, NULL, &TelemetryTask_attributes);

  /* creation of StatusTask */
  StatusTaskHandle = osThreadNew(StartStatusTask, NULL, &StatusTask_attributes);

  /* USER CODE BEGIN RTOS_THREADS */
  /* add threads, ... */
  /* USER CODE END RTOS_THREADS */

  /* USER CODE BEGIN RTOS_EVENTS */
  /* add events, ... */
  /* USER CODE END RTOS_EVENTS */

  /* Start scheduler */
  osKernelStart();

  /* We should never get here as control is now taken by the scheduler */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */

  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE2);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI|RCC_OSCILLATORTYPE_LSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.LSIState = RCC_LSI_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 16;
  RCC_OscInitStruct.PLL.PLLN = 336;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV4;
  RCC_OscInitStruct.PLL.PLLQ = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief I2C1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_I2C1_Init(void)
{

  /* USER CODE BEGIN I2C1_Init 0 */

  /* USER CODE END I2C1_Init 0 */

  /* USER CODE BEGIN I2C1_Init 1 */

  /* USER CODE END I2C1_Init 1 */
  hi2c1.Instance = I2C1;
  hi2c1.Init.ClockSpeed = 100000;
  hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c1.Init.OwnAddress1 = 0;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN I2C1_Init 2 */

  /* USER CODE END I2C1_Init 2 */

}

/**
  * @brief IWDG Initialization Function
  * @param None
  * @retval None
  */
static void MX_IWDG_Init(void)
{

  /* USER CODE BEGIN IWDG_Init 0 */

  /* USER CODE END IWDG_Init 0 */

  /* USER CODE BEGIN IWDG_Init 1 */

  /* USER CODE END IWDG_Init 1 */
  hiwdg.Instance = IWDG;
  hiwdg.Init.Prescaler = IWDG_PRESCALER_32;
  hiwdg.Init.Reload = 4000;
  if (HAL_IWDG_Init(&hiwdg) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN IWDG_Init 2 */

  /* USER CODE END IWDG_Init 2 */

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * Enable DMA controller clock
  */
static void MX_DMA_Init(void)
{

  /* DMA controller clock enable */
  __HAL_RCC_DMA1_CLK_ENABLE();

  /* DMA interrupt init */
  /* DMA1_Stream6_IRQn interrupt configuration */
  HAL_NVIC_SetPriority(DMA1_Stream6_IRQn, 5, 0);
  HAL_NVIC_EnableIRQ(DMA1_Stream6_IRQn);

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOH_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pin Output Level */
  HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

  /*Configure GPIO pin : B1_Pin */
  GPIO_InitStruct.Pin = B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_IT_FALLING;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(B1_GPIO_Port, &GPIO_InitStruct);

  /*Configure GPIO pin : LD2_Pin */
  GPIO_InitStruct.Pin = LD2_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(LD2_GPIO_Port, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */
uint8_t calculate_checksum(char* str, int len) {
    uint8_t checksum = 0;
    for (int i = 0; i < len; i++) {
        checksum ^= (uint8_t)str[i];
    }
    return checksum;
}

// UART RX Interrupt Callback (Invoked automatically by hardware when a byte arrives)
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2)
    {

        // Send the received byte into the FreeRTOS queue.  These counters are
        // included in the heartbeat so a host-to-board wiring problem is
        // visible instead of silently leaving the rate at its old value.
        if (cmdQueueHandle != NULL && osMessageQueuePut(cmdQueueHandle, &rx_byte, 0, 0) == osOK)
        {
            command_rx_count++;
        }
        else
        {
            command_rx_drop_count++;
        }

        // Re-arm interrupt for the next incoming byte
        HAL_UART_Receive_IT(&huart2, &rx_byte, 1);
    }
}
/* USER CODE END 4 */

/* USER CODE BEGIN Header_StartDefaultTask */
/**
  * @brief  Function implementing the TelemetryTask thread.
  * @param  argument: Not used
  * @retval None
  */
/* USER CODE END Header_StartDefaultTask */
void StartDefaultTask(void *argument)
{
  /* USER CODE BEGIN 5 */
	static char uart_buf[100];
	uint32_t next_wake = osKernelGetTickCount();

  /* Infinite loop */
	for(;;)
	  {
	    HAL_IWDG_Refresh(&hiwdg);

	    if (!telemetry_paused)
	    {
            MPU6050_Read_All();

		float mag_sq = (Ax * Ax) + (Ay * Ay) + (Az * Az);

		if (mag_sq > CRASH_THRESHOLD_SQ && !crash_logged)
		{
			// Turn ON onboard green LED to signal a triggered write
			HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_SET);

			Write_Crash_Log(Ax, Ay, Az);
			crash_logged = 1; // Prevent continuous flash writes
		}

	        if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
	        {
	            // Wait for any background DMA transfer to finish
	            while (huart2.gState != HAL_UART_STATE_READY)
	            {
	                osDelay(1); // Yield CPU to other tasks while waiting
	            }

	            // NOW it is 100% safe to overwrite the buffer
	            int data_len = sprintf(uart_buf, "AX:%.2f|AY:%.2f|AZ:%.2f|GX:%.1f|GY:%.1f|GZ:%.1f", Ax, Ay, Az, Gx, Gy, Gz);
	            uint8_t chk = calculate_checksum(uart_buf, data_len);
	            int total_len = sprintf(uart_buf + data_len, "|CHK:0x%02X\r\n", chk);

	            HAL_UART_Transmit_DMA(&huart2, (uint8_t *)uart_buf, data_len + total_len);

	            osMutexRelease(uartMutexHandle);
	        }
	    }

	    // Keep a stable deadline instead of adding sensor-read and DMA-start
	    // overhead to every period.  If a slow I2C transaction overruns the
	    // deadline, restart from the current tick rather than burst-catch-up.
	    uint32_t period_ms = telemetry_delay_ms;
	    next_wake += period_ms;
	    uint32_t now_ticks = osKernelGetTickCount();
	    if ((int32_t)(now_ticks - next_wake) >= 0)
	    {
	        next_wake = now_ticks + period_ms;
	    }
	    osDelayUntil(next_wake);
	  }
  /* USER CODE END 5 */
}

/* USER CODE BEGIN Header_StartStatusTask */
/**
* @brief Function implementing the StatusTask thread.
* @param argument: Not used
* @retval None
*/
/* USER CODE END Header_StartStatusTask */
void StartStatusTask(void *argument)
{
  /* USER CODE BEGIN StartStatusTask */
  static char status_buf[100];
  uint8_t received_cmd = 0;

  /* Infinite loop */
  for(;;)
  {
    // Process ALL queued command bytes sent from the UART RX ISR
    while (osMessageQueueGet(cmdQueueHandle, &received_cmd, NULL, 0) == osOK)
    {
        // Filter out line endings and whitespace sent by serial terminal apps
        if (received_cmd != '\r' && received_cmd != '\n' && received_cmd != ' ')
        {
            // ----------------------------------------------------------------
            // COMMAND 'p' : PAUSE / RESUME TELEMETRY (HW POWER MANAGEMENT)
            // ----------------------------------------------------------------
            if (received_cmd == 'p')
            {
                telemetry_paused = !telemetry_paused;

                if (telemetry_paused)
                {
                    MPU6050_Sleep(); // Drop sensor to 5 uA sleep state over I2C
                }
                else
                {
                    MPU6050_Wake();  // Restore active sampling mode
                }

                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }

                    int len = sprintf(status_buf, "[CMD RECV]: Telemetry %s (Sensor %s)\r\n",
                                      telemetry_paused ? "PAUSED" : "RESUMED",
                                      telemetry_paused ? "SLEEP" : "AWAKE");

                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 'f' : FAST SAMPLING RATE (500ms / 2Hz)
            // ----------------------------------------------------------------
            else if (received_cmd == 'f')
            {
                telemetry_delay_ms = TELEMETRY_RATE_FAST_MS;
                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }
                    int len = sprintf(status_buf, "[CMD RECV]: Sampling Rate set to FAST (500ms)\r\n");
                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 'n' : NORMAL SAMPLING RATE (1000ms / 1Hz)
            // ----------------------------------------------------------------
            else if (received_cmd == 'n')
            {
                telemetry_delay_ms = TELEMETRY_RATE_NORMAL_MS;
                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }
                    int len = sprintf(status_buf, "[CMD RECV]: Sampling Rate set to NORMAL (1000ms)\r\n");
                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 's' : SLOW SAMPLING RATE (2000ms / 0.5Hz)
            // ----------------------------------------------------------------
            else if (received_cmd == 's')
            {
                telemetry_delay_ms = TELEMETRY_RATE_SLOW_MS;
                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }
                    int len = sprintf(status_buf, "[CMD RECV]: Sampling Rate set to SLOW (2000ms)\r\n");
                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 'v' : 33 FPS VISUAL SAMPLING RATE (30ms)
            // ----------------------------------------------------------------
            else if (received_cmd == 'v')
            {
                telemetry_delay_ms = TELEMETRY_RATE_VISUAL_MS;
                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }
                    int len = sprintf(status_buf, "[CMD RECV]: Sampling Rate set to VISUAL (30ms / 33Hz)\r\n");
                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 'c' : CLEAR / ERASE FLASH BLACK BOX SECTOR
            // ----------------------------------------------------------------
            else if (received_cmd == 'c')
            {
                Clear_Crash_Log();
                crash_logged = 0; // Re-arm shock detection trigger

                HAL_GPIO_WritePin(LD2_GPIO_Port, LD2_Pin, GPIO_PIN_RESET);

                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }
                    int len = sprintf(status_buf, "[CMD RECV]: Flash Sector Erased. Crash Log Cleared.\r\n");
                    HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    osMutexRelease(uartMutexHandle);
                }
            }
            // ----------------------------------------------------------------
            // COMMAND 'd' : DUMP BLACK BOX SNAPSHOT FROM FLASH MEMORY
            // ----------------------------------------------------------------
            else if (received_cmd == 'd')
            {
                if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
                {
                    while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }

                    // Read 32-bit Magic Word directly from Sector 5 memory address
                    uint32_t magic = *(__IO uint32_t*)FLASH_USER_SECTOR_ADDR;

                    if (magic == CRASH_MAGIC_WORD)
                    {
                        // Direct pointer dereferencing to cast raw Flash words back into floats
                        float read_x = *(float*)(FLASH_USER_SECTOR_ADDR + 4);
                        float read_y = *(float*)(FLASH_USER_SECTOR_ADDR + 8);
                        float read_z = *(float*)(FLASH_USER_SECTOR_ADDR + 12);

                        int len = sprintf(status_buf, "[FLASH LOG]: CRASH SNAPSHOT -> X: %.2f | Y: %.2f | Z: %.2f\r\n",
                                          read_x, read_y, read_z);
                        HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    }
                    else
                    {
                        int len = sprintf(status_buf, "[FLASH LOG]: Sector Empty. No crashes recorded.\r\n");
                        HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);
                    }

                    osMutexRelease(uartMutexHandle);
                }
            }
        }
    }

    // Print a periodic system health heartbeat every 5 seconds.
    // RX should increment when the PC sends a rate command.
    if (uartMutexHandle != NULL && osMutexAcquire(uartMutexHandle, 100) == osOK)
    {
        while (huart2.gState != HAL_UART_STATE_READY) { osDelay(1); }

        int len = sprintf(status_buf,
                          "[SYS STATUS]: RTOS Nominal | Watchdog Active | Rate: %lums | RX: %lu | Drop: %lu\r\n",
                          telemetry_delay_ms, command_rx_count, command_rx_drop_count);
        HAL_UART_Transmit_DMA(&huart2, (uint8_t *)status_buf, len);

        osMutexRelease(uartMutexHandle);
    }

    osDelay(5000); // Yield CPU execution to TelemetryTask for 5 seconds
  }
  /* USER CODE END StartStatusTask */
}

/**
  * @brief  Period elapsed callback in non blocking mode
  * @note   This function is called  when TIM11 interrupt took place, inside
  * HAL_TIM_IRQHandler(). It makes a direct call to HAL_IncTick() to increment
  * a global variable "uwTick" used as application time base.
  * @param  htim : TIM handle
  * @retval None
  */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  /* USER CODE BEGIN Callback 0 */

  /* USER CODE END Callback 0 */
  if (htim->Instance == TIM11)
  {
    HAL_IncTick();
  }
  /* USER CODE BEGIN Callback 1 */

  /* USER CODE END Callback 1 */
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
